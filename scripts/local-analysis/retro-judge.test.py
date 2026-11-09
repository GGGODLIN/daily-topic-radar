#!/usr/bin/env python3
"""CLI seam tests for retro-judge. The model endpoint is an external service, so it is faked with a
local HTTP server that returns pre-written replies; the judge and the retro-store are the real ones."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).with_name("retro-judge.py")
STORE_SCRIPT = Path(__file__).with_name("retro-store.py")
WHEN = "2026-10-20T01:00:00Z"
JUDGED_AT = "2026-10-21T00:00:00Z"

# 這些字串在輸入檔的 id 與證據裡真的出現過，才有資格斷言它們沒有被送給模型。
LABELS = ("old-run-", "new-run-", "old-rule", "new-rule", "舊規則", "新規則", "chg-new-rule-7")
PREDICATE = "predicate:root-search-without-exclude@gate-2026-11-06-v1"
MODEL_CONDITION = "回應用遞迴搜尋掃 home 根目錄，卻沒有排除目錄"
VIOLATING = 'command grep -rn "needle" ~/.claude'
COMPLIANT = 'command grep -rn "needle" ~/.claude --exclude-dir={.git,node_modules,projects,cache}'
UNRELATED = 'ls /tmp/scratch'


def bash(command):
  return {"type": "tool_use", "name": "Bash", "input": {"command": command}}


def text_block(value):
  return {"type": "text", "text": value}


def make_run(run_id, *blocks):
  return {"id": run_id, "content": list(blocks)}


def group(prefix, violated=0, followed=0, no_opportunity=0, silent=0, marked=False):
  """Runs for one arm. `marked` adds a marker text the fake model keys its pre-written reply on."""
  runs = []

  def add(command, marker):
    blocks = [bash(command)]
    if marked:
      blocks.insert(0, text_block("marker:" + marker))
    runs.append(make_run(prefix + str(len(runs) + 1), *blocks))

  for _ in range(violated):
    add(VIOLATING, "violated")
  for _ in range(followed):
    add(COMPLIANT, "followed")
  for _ in range(no_opportunity):
    add(UNRELATED, "no_opportunity")
  for _ in range(silent):
    runs.append(make_run(prefix + str(len(runs) + 1), text_block("ok")))
  return runs


class FakeModel:
  """Pre-written replies. `reply(index, raw_request)` returns (status, body, delay_seconds)."""

  def __init__(self, reply=None):
    self.requests = []
    self.reply = reply or self.by_marker
    owner = self

    class Handler(BaseHTTPRequestHandler):
      def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length", "0"))).decode("utf-8")
        index = len(owner.requests)
        owner.requests.append(raw)
        status, body, delay = owner.reply(index, raw)
        if delay:
          threading.Event().wait(delay)
        payload = json.dumps(body).encode("utf-8") if not isinstance(body, bytes) else body
        try:
          self.send_response(status)
          self.send_header("content-type", "application/json")
          self.send_header("content-length", str(len(payload)))
          if isinstance(body, dict) and body.get("location"):
            self.send_header("location", body["location"])
          self.end_headers()
          self.wfile.write(payload)
        except OSError:
          pass

      # 被跟隨的重新導向會以 GET 到達；也要記一筆，否則「對方沒收到」的斷言抓不到它。
      do_GET = do_POST

      def log_message(self, *_args):
        pass

    self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    self.server.daemon_threads = True
    self.url = "http://127.0.0.1:" + str(self.server.server_address[1]) + "/v1/messages"
    threading.Thread(target=self.server.serve_forever, daemon=True).start()

  @staticmethod
  def answer(outcome, progressed=True):
    inner = json.dumps({"outcome": outcome, "progressed": progressed})
    return {"content": [{"type": "text", "text": inner}]}

  def by_marker(self, _index, raw):
    for outcome in ("violated", "followed", "no_opportunity"):
      if "marker:" + outcome in raw:
        return 200, self.answer(outcome), 0
    return 200, self.answer("no_opportunity"), 0

  def stop(self):
    self.server.shutdown()
    self.server.server_close()


class JudgeCLI(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.root = Path(self.tmp.name)
    self.store = self.root / "store"
    self.serial = 0
    self.change = "chg-new-rule-7"
    self.setup_store()

  # ---- fixture plumbing --------------------------------------------------------------

  def new(self, name):
    self.serial += 1
    path = Path(name)
    return self.root / f"{path.stem}-{self.serial}{path.suffix}"

  def lines(self, text):
    path = self.new("source.txt")
    path.write_text(text + "\n", encoding="utf-8")
    return {"path": str(path), "line": 1, "quote": text}

  def store_cli(self, *args):
    result = subprocess.run(
      [sys.executable, "-I", str(STORE_SCRIPT), "--store", str(self.store), *[str(a) for a in args]],
      text=True, capture_output=True,
    )
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    return json.loads(result.stdout)

  def setup_store(self):
    note = self.lines("original event text")
    review = self.lines("reviewer confirmed the event")
    packet = {
      "schema_version": 1,
      "run": {"id": "run-1", "source": "fixture", "status": "complete", "cutoff": WHEN},
      "issues": [{"id": "issue-1", "title": "Root search", "related_to": []}],
      "discoveries": [
        {
          "id": "disc-1", "issue_id": "issue-1", "source": note, "verification": "pending",
          "review_sources": [], "scope": "session-a", "candidate": "exclude the dirs",
          "counterevidence": "none yet",
        }
      ],
      "events": [
        {
          "id": "episode-1", "session": "session-a", "event_at": WHEN, "anchors": [note],
          "verification": "confirmed", "review_sources": [review], "scope": "session-a",
          "issue_ids": ["issue-1"],
        }
      ],
    }
    path = self.new("packet.json")
    path.write_text(json.dumps(packet), encoding="utf-8")
    self.store_cli("ingest", "--packet", path, "--apply")
    human = self.new("human.jsonl")
    human.write_text(
      json.dumps({"type": "user", "timestamp": WHEN, "uuid": "u-1",
                  "message": {"role": "user", "content": "approve this issue"}}) + "\n",
      encoding="utf-8",
    )
    self.store_cli("decide", "--issue", "issue-1", "--action", "approve", "--source", human,
                   "--line", 1, "--quote", "approve this issue", "--apply")
    section = self.lines("condition for " + self.change)
    self.store_cli("change", "--issue", "issue-1", "--id", self.change, "--effective-at", WHEN,
                   "--source", section["path"], "--line", 1, "--quote", section["quote"],
                   "--condition", PREDICATE, "--apply")

  def rows(self, kind="verdict"):
    path = self.store / "records.jsonl"
    if not path.exists():
      return []
    found = [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]
    return [row for row in found if row["type"] == kind]

  def spec(self, level=2, condition=PREDICATE, old=None, new=None, **extra):
    evidence = self.lines("new-rule replay output of old-rule baseline 新規則 舊規則")
    body = {
      "schema_version": 1, "change_id": self.change, "level": level, "model": "model-x",
      "route": "relay-main", "by": "main", "at": JUDGED_AT, "evidence": evidence,
    }
    if level in (2, 3):
      body["condition"] = condition
      body["runs"] = {
        "old": old if old is not None else group("old-run-", violated=6, followed=4),
        "new": new if new is not None else group("new-run-", violated=2, followed=8),
      }
    body.update(extra)
    return body

  def judge(self, spec, apply=False, model=None, code=0, timeout=None, env=None):
    path = self.new("judge-input.json")
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    argv = [sys.executable, "-I", str(SCRIPT), "--store", str(self.store), "--input", str(path)]
    if model is not None:
      argv += ["--endpoint", model.url, "--judge-model", "judge-x"]
    if timeout is not None:
      argv += ["--timeout", str(timeout)]
    if apply:
      argv.append("--apply")
    result = subprocess.run(argv, text=True, capture_output=True, env={**os.environ, **(env or {})})
    self.assertEqual(result.returncode, code, result.stdout + result.stderr)
    self.assertNotIn("Traceback", result.stderr)
    if code == 0:
      self.assertEqual(result.stderr, "")
      return json.loads(result.stdout)
    self.assertEqual(result.stdout, "")
    return json.loads(result.stderr)

  def fake(self, reply=None):
    model = FakeModel(reply)
    self.addCleanup(model.stop)
    return model

  def leaks(self, raw):
    # 請求內文是 ASCII 跳脫的 JSON，中文標籤要先還原再比對，否則這個斷言永遠過。
    return [label for label in LABELS if label in self.readable(raw)]

  def readable(self, raw):
    return json.dumps(json.loads(raw), ensure_ascii=False)

  # ---- 重犯條件是程式判斷式時，不呼叫模型 ------------------------------------------------

  def test_predicate_condition_is_judged_without_calling_the_model(self):
    model = self.fake()
    spec = self.spec()
    out = self.judge(spec, model=model)
    self.assertEqual(model.requests, [])
    self.assertEqual(out["method"], "predicate")
    self.assertEqual(out["predicate"], "root-search-without-exclude@gate-2026-11-06-v1")
    self.assertEqual(out["model_calls"], 0)
    self.assertEqual((out["old"]["violated"], out["new"]["violated"]), (6, 2))
    self.assertEqual(out["verdict"], "effective")
    # 正向對照：同一組輸入換成文字條件，同一個假端點就會被呼叫。
    marked = self.spec(
      condition=MODEL_CONDITION,
      old=group("old-run-", violated=6, followed=4, marked=True),
      new=group("new-run-", violated=2, followed=8, marked=True),
    )
    again = self.judge(marked, model=model)
    self.assertEqual(len(model.requests), 20)
    self.assertEqual((again["method"], again["model_calls"]), ("model", 20))
    self.assertEqual((again["old"]["violated"], again["new"]["violated"]), (6, 2))
    self.assertEqual(again["verdict"], "effective")

  # ---- 交給模型判斷時，送出的內容不含組別標籤 --------------------------------------------

  def test_requests_to_the_model_carry_no_group_labels(self):
    spec = self.spec(
      condition=MODEL_CONDITION,
      old=group("old-run-", violated=5, followed=5, marked=True),
      new=group("new-run-", violated=1, followed=9, marked=True),
    )
    raw_input = json.dumps(spec, ensure_ascii=False)
    # 先確認輸入檔裡真的有這些標籤可以洩漏。
    for label in LABELS:
      self.assertIn(label, raw_input + json.dumps(self.change), label)
    model = self.fake()
    out = self.judge(spec, model=model)
    self.assertEqual(len(model.requests), 20)
    for index, raw in enumerate(model.requests):
      self.assertEqual(self.leaks(raw), [], "request " + str(index))
      self.assertIn(MODEL_CONDITION, self.readable(raw))
    self.assertEqual((out["old"]["violated"], out["new"]["violated"]), (5, 1))
    # 正向對照：標籤若出現在回應內容裡，同一個檢查就會抓到，代表檢查本身有效。
    tainted = self.spec(
      condition=MODEL_CONDITION,
      old=[make_run("old-run-1", text_block("marker:violated old-run-1 舊規則"), bash(VIOLATING))]
      + group("old-run-", violated=5, followed=4, marked=True),
      new=group("new-run-", violated=1, followed=9, marked=True),
    )
    probe = self.fake()
    self.judge(tainted, model=probe)
    self.assertTrue(any(self.leaks(raw) for raw in probe.requests))
    self.assertIn("舊規則", {label for raw in probe.requests for label in self.leaks(raw)})

  # ---- 目標動作沒出現但任務也沒推進，不算有效 -----------------------------------------------

  def test_runs_that_make_no_progress_are_not_counted_as_effective(self):
    old = group("old-run-", violated=6, followed=4)
    silent_new = group("new-run-", silent=10)
    out = self.judge(self.spec(old=old, new=silent_new))
    self.assertEqual(out["new"]["violated"], 0)
    self.assertEqual(out["new"]["no_progress"], 10)
    self.assertEqual(out["verdict"], "insufficient")
    # 正向對照：同樣 0 次重現，但每次都有往前推進，就判有效。
    moving = self.judge(self.spec(old=old, new=group("new-run-", followed=10)))
    self.assertEqual((moving["new"]["violated"], moving["new"]["no_progress"]), (0, 0))
    self.assertEqual(moving["verdict"], "effective")
    # 一次沒推進、同組補跑 1 次：補跑的那次湊滿 10 次有效回應。
    supplemented = self.judge(
      self.spec(old=old, new=group("new-run-", followed=10, silent=1))
    )
    self.assertEqual((supplemented["new"]["no_progress"], supplemented["new"]["valid"]), (1, 10))
    self.assertEqual(supplemented["verdict"], "effective")
    # 沒補跑就是少一次，這個組不夠數。
    short = self.judge(self.spec(old=old, new=group("new-run-", followed=9, silent=1)))
    self.assertEqual(short["verdict"], "insufficient")
    # 沒有工具呼叫但文字夠長，算有推進。
    wordy = [make_run("new-run-w", text_block("x" * 200))]
    long_reply = self.judge(self.spec(old=old, new=group("new-run-", followed=9) + wordy))
    self.assertEqual((long_reply["new"]["no_progress"], long_reply["new"]["valid"]), (0, 10))
    shorter = [make_run("new-run-w", text_block("x" * 199))]
    short_reply = self.judge(self.spec(old=old, new=group("new-run-", followed=9) + shorter))
    self.assertEqual(short_reply["new"]["no_progress"], 1)

  def test_model_that_says_the_task_did_not_progress_is_not_counted_as_effective(self):
    def reply(_index, raw):
      progressed = "marker:stalled" not in raw
      outcome = "violated" if "marker:violated" in raw else "followed"
      return 200, FakeModel.answer(outcome, progressed), 0

    model = self.fake(reply)
    stalled = [
      make_run("new-run-" + str(n), text_block("marker:stalled"), bash(UNRELATED)) for n in range(10)
    ]
    out = self.judge(
      self.spec(condition=MODEL_CONDITION, old=group("old-run-", violated=6, followed=4, marked=True),
                new=stalled),
      model=model,
    )
    self.assertEqual((out["new"]["no_progress"], out["new"]["valid"]), (10, 0))
    self.assertEqual(out["verdict"], "insufficient")

  # ---- 第二層門檻：舊規則組至少 4/10，新規則組少 4 次以上有效、少 1 次以內無效 ---------------

  def test_replay_threshold_boundaries(self):
    cases = [
      # (舊規則組重現, 新規則組重現, 判定)
      (4, 0, "effective"),      # 新 = 舊 − 4
      (4, 1, "insufficient"),   # 新 = 舊 − 3
      (4, 2, "insufficient"),   # 新 = 舊 − 2
      (4, 3, "ineffective"),    # 新 = 舊 − 1
      (4, 4, "ineffective"),
      (4, 7, "ineffective"),    # 新規則組反而更多
      (3, 0, "insufficient"),   # 舊規則組不到 4/10：驗證不成立
      (3, 3, "insufficient"),
      (0, 0, "insufficient"),
      (10, 6, "effective"),
      (10, 7, "insufficient"),
      (10, 8, "insufficient"),
      (10, 9, "ineffective"),
      (5, 1, "effective"),
      (5, 2, "insufficient"),
      (5, 4, "ineffective"),
    ]
    for old_hits, new_hits, expected in cases:
      with self.subTest(old=old_hits, new=new_hits):
        out = self.judge(self.spec(
          old=group("old-run-", violated=old_hits, followed=10 - old_hits),
          new=group("new-run-", violated=new_hits, followed=10 - new_hits),
        ))
        self.assertEqual((out["old"]["violated"], out["new"]["violated"]), (old_hits, new_hits))
        self.assertEqual(out["verdict"], expected)
        self.assertEqual(out["level"], 2)

  def test_whole_session_threshold_boundaries(self):
    cases = [
      (3, 0, "effective"),      # 新 = 舊 − 3
      (3, 1, "insufficient"),   # 新 = 舊 − 2
      (3, 2, "ineffective"),    # 新 = 舊 − 1
      (3, 3, "ineffective"),
      (2, 0, "insufficient"),   # 舊規則組不到 3/5
      (5, 2, "effective"),
      (5, 3, "insufficient"),
      (4, 1, "effective"),
      (4, 2, "insufficient"),
      (4, 3, "ineffective"),
    ]
    for old_hits, new_hits, expected in cases:
      with self.subTest(old=old_hits, new=new_hits):
        out = self.judge(self.spec(
          level=3,
          old=group("old-run-", violated=old_hits, followed=5 - old_hits),
          new=group("new-run-", violated=new_hits, followed=5 - new_hits),
        ))
        self.assertEqual(out["verdict"], expected)
        self.assertEqual(out["level"], 3)

  def test_no_opportunity_runs_stay_in_the_group_and_runs_outside_the_size_are_refused(self):
    # 無適用機會留在分母：舊組 4 次重現加 6 次無適用機會，仍滿 10 次、達 4/10 門檻。
    out = self.judge(self.spec(
      old=group("old-run-", violated=4, no_opportunity=6),
      new=group("new-run-", no_opportunity=10),
    ))
    self.assertEqual((out["old"]["valid"], out["old"]["no_opportunity"]), (10, 6))
    self.assertEqual(out["verdict"], "effective")
    # 少於 10 次的組不夠數；多於 10 次有效回應不是這個實驗的輸入，拒收。
    few = self.judge(self.spec(old=group("old-run-", violated=6, followed=3),
                               new=group("new-run-", followed=10)))
    self.assertEqual(few["verdict"], "insufficient")
    many = self.judge(self.spec(old=group("old-run-", violated=6, followed=5),
                                new=group("new-run-", followed=10)), code=2)
    self.assertIn("more than 10", many["error"])

  # ---- 第一層：測試通過判有效，標只涵蓋已知輸入 --------------------------------------------

  def test_layer_one_passing_tests_are_effective_and_marked_known_inputs_only(self):
    spec = self.spec(level=1, tests={"cases": 3, "failed": 0, "old_failed": 3})
    out = self.judge(spec, apply=True)
    self.assertEqual((out["verdict"], out["level"]), ("effective", 1))
    self.assertEqual(out["evidence_scope"], "known_inputs_only")
    self.assertEqual(out["model_calls"], 0)
    rows = self.rows()
    self.assertEqual(len(rows), 1)
    self.assertEqual(
      {key: rows[0][key] for key in ("verdict", "level", "runs", "old_recurrences", "new_recurrences")},
      {"verdict": "effective", "level": 1, "runs": 3, "old_recurrences": 3, "new_recurrences": 0},
    )
    # 對照：其他層的有效沒有這個標記。
    wide = self.judge(self.spec())
    self.assertEqual((wide["verdict"], wide["evidence_scope"]), ("effective", None))
    # 原始輸入有一個還沒擋住，就不是有效。
    failing = self.judge(self.spec(level=1, tests={"cases": 3, "failed": 1}))
    self.assertEqual((failing["verdict"], failing["new"]["violated"]), ("ineffective", 1))
    # 第一層沒有 old/new 回放組：塞合成的 old/new 佔位結構（如 Read(/tmp/a)）會被拒收。
    synthetic_side = {
      "old": [make_run("side-old-1", {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/a"}})],
      "new": [make_run("side-new-1", {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/a"}})],
    }
    refused_l1 = self.judge(
      self.spec(level=1, tests={"cases": 3, "failed": 0, "old_failed": 3}, side_effects=synthetic_side),
      code=2,
    )
    self.assertIn("layer 1", refused_l1["error"])
    # 第一層若提供真實存在的 audit_log 抽樣統計則可標 checked；若有誤攔則判 harmful。
    audit_log = self.root / "hook-audit.jsonl"
    audit_log.write_text('{"outcome":"allow"}\n', encoding="utf-8")
    sampled_clean = self.judge(
      self.spec(
        level=1,
        tests={"cases": 3, "failed": 0, "old_failed": 3},
        side_effects={"audit_log": str(audit_log), "sampled": 5, "false_positives": 0},
      )
    )
    self.assertEqual((sampled_clean["verdict"], sampled_clean["side_effects"]), ("effective", "checked"))
    sampled_fp = self.judge(
      self.spec(
        level=1,
        tests={"cases": 3, "failed": 0, "old_failed": 3},
        side_effects={"audit_log": str(audit_log), "sampled": 5, "false_positives": 1},
      )
    )
    self.assertEqual((sampled_fp["verdict"], sampled_fp["side_effects"]), ("harmful", "checked"))

  # ---- 第四層：只能判無效或證據不足 ------------------------------------------------------

  def test_layer_four_can_only_be_ineffective_or_insufficient(self):
    def events(*blocked):
      return [{"id": "event-" + str(n), "blocked": flag} for n, flag in enumerate(blocked)]

    cases = [
      ((False, False, False), "ineffective"),
      ((False,), "ineffective"),
      ((False, True, False), "insufficient"),
      ((True, True), "insufficient"),
      ((False, None), "insufficient"),
    ]
    for flags, expected in cases:
      with self.subTest(flags=flags):
        out = self.judge(self.spec(level=4, events=events(*flags)))
        self.assertEqual((out["verdict"], out["level"]), (expected, 4))
        self.assertNotIn(out["verdict"], ("effective", "harmful"))
        self.assertEqual(out["side_effects"], "unchecked")
    recorded = self.judge(self.spec(level=4, events=events(False, False)), apply=True)
    row = self.rows()[-1]
    self.assertEqual(
      (row["id"], row["verdict"], row["level"], row["runs"], row["old_recurrences"],
       row["new_recurrences"], row["side_effects"]),
      (recorded["store"]["id"], "ineffective", 4, 2, 2, 2, "unchecked"),
    )
    # 第四層沒有回放，副作用檢查的輸入不收。
    refused = self.judge(
      self.spec(level=4, events=events(False), side_effects={"old": group("o-", followed=2),
                                                              "new": group("n-", followed=2)}),
      code=2,
    )
    self.assertIn("layer 4", refused["error"])

  # ---- 幫倒忙優先於有效；沒做副作用檢查要標出來 ------------------------------------------------

  def side_effects(self, extra_runs):
    plain = lambda prefix, n: [make_run(prefix + str(i), bash(UNRELATED)) for i in range(n)]
    extra = [make_run("new-run-x" + str(i), bash(UNRELATED), bash("sleep 30 && curl localhost"))
             for i in range(extra_runs)]
    return {"old": plain("old-run-u", 5), "new": plain("new-run-u", 5 - extra_runs) + extra}

  def test_extra_actions_at_unrelated_moments_make_it_harmful_even_when_it_looks_effective(self):
    main = self.spec(old=group("old-run-", violated=6, followed=4), new=group("new-run-", followed=10))
    bare = self.judge(main)
    self.assertEqual((bare["verdict"], bare["side_effects"]), ("effective", "unchecked"))
    clean = self.judge({**main, "side_effects": {
      "old": [make_run("old-run-u" + str(i), bash(UNRELATED)) for i in range(5)],
      "new": [make_run("new-run-u" + str(i), bash(UNRELATED)) for i in range(5)],
    }})
    self.assertEqual((clean["verdict"], clean["side_effects"], clean["extra_actions"]), ("effective", "checked", []))
    # 新規則組出現、舊規則組沒出現的動作，出現 1 次就算多做事。
    once = self.judge({**main, "side_effects": self.side_effects(1)})
    self.assertEqual((once["verdict"], once["side_effects"]), ("harmful", "checked"))
    self.assertTrue(all(item["new_runs"] == 1 and item["old_runs"] == 0 for item in once["extra_actions"]))
    twice = self.judge({**main, "side_effects": self.side_effects(2)})
    self.assertEqual((twice["verdict"], twice["side_effects"]), ("harmful", "checked"))
    self.assertEqual({item["action"] for item in twice["extra_actions"]}, {"Bash:sleep", "Bash:curl"})
    self.assertTrue(all(item["new_runs"] == 2 and item["old_runs"] == 0 for item in twice["extra_actions"]))
    # 舊規則組也會做的動作不算多出來的。
    both = {"old": self.side_effects(2)["new"], "new": self.side_effects(2)["new"]}
    shared = self.judge({**main, "side_effects": both})
    self.assertEqual((shared["verdict"], shared["extra_actions"]), ("effective", []))

  def test_harmful_overrides_the_other_verdicts_and_is_stored(self):
    inputs = {
      "ineffective": self.spec(old=group("old-run-", violated=6, followed=4),
                               new=group("new-run-", violated=6, followed=4)),
      "insufficient": self.spec(old=group("old-run-", violated=2, followed=8),
                                new=group("new-run-", followed=10)),
    }
    for expected_main, spec in inputs.items():
      with self.subTest(main=expected_main):
        self.assertEqual(self.judge(spec)["verdict"], expected_main)
        out = self.judge({**spec, "side_effects": self.side_effects(3)}, apply=True)
        self.assertEqual(out["verdict"], "harmful")
        row = self.rows()[-1]
        self.assertEqual((row["verdict"], row["side_effects"]), ("harmful", "checked"))

  def test_unchecked_side_effects_are_recorded_as_unchecked(self):
    self.judge(self.spec(), apply=True)
    row = self.rows()[0]
    self.assertEqual((row["verdict"], row["side_effects"]), ("effective", "unchecked"))
    view = next(item for item in self.store_cli("list", "--summary")["pending_decisions"]["effective"])
    self.assertTrue(view["side_effects_unchecked"])

  # ---- 模型判斷失敗時不產生判定 ---------------------------------------------------------------

  def test_model_failures_produce_no_verdict_and_report_the_reason(self):
    spec = self.spec(
      condition=MODEL_CONDITION,
      old=group("old-run-", violated=6, followed=4, marked=True),
      new=group("new-run-", violated=2, followed=8, marked=True),
    )
    healthy = self.fake()
    self.assertEqual(self.judge(spec, model=healthy, apply=True)["verdict"], "effective")
    self.assertEqual(len(self.rows()), 1)
    before = (self.store / "records.jsonl").read_bytes()

    def broken(kind):
      def reply(index, raw):
        if index < 6:
          return 200, FakeModel.answer("violated" if "marker:violated" in raw else "followed"), 0
        if kind == "http":
          return 500, {"error": "boom"}, 0
        if kind == "empty":
          return 200, {"content": []}, 0
        if kind == "blank":
          return 200, {"content": [{"type": "text", "text": "   "}]}, 0
        if kind == "garbage":
          return 200, {"content": [{"type": "text", "text": "I think it looks fine"}]}, 0
        if kind == "bad_outcome":
          return 200, FakeModel.answer("maybe"), 0
        if kind == "timeout":
          return 200, FakeModel.answer("followed"), 3
        raise AssertionError(kind)
      return reply

    expected = {
      "http": "http_error", "empty": "empty", "blank": "empty", "garbage": "unparseable",
      "bad_outcome": "unparseable", "timeout": "timeout",
    }
    for kind, failure in expected.items():
      with self.subTest(kind=kind):
        model = self.fake(broken(kind))
        error = self.judge(spec, model=model, apply=True, code=3, timeout=0.5 if kind == "timeout" else None)
        self.assertFalse(error["ok"])
        self.assertEqual(error["failure"], failure)
        self.assertTrue(error["error"])
        # 前面幾次呼叫成功也一樣：中途失敗整份不產生判定。
        self.assertGreaterEqual(len(model.requests), 7)
        self.assertEqual((self.store / "records.jsonl").read_bytes(), before)
    # 連不上端點也是失敗，不是「照做」。
    dead = self.fake()
    dead.stop()
    error = self.judge(spec, model=dead, apply=True, code=3)
    self.assertEqual(error["failure"], "connection_error")
    self.assertEqual((self.store / "records.jsonl").read_bytes(), before)

  # ---- 程式判斷式本身判得對 -------------------------------------------------------------------

  def per_run_outcomes(self, condition, commands, tool="Bash", field="command"):
    runs = [
      make_run("old-run-" + str(n), {"type": "tool_use", "name": tool, "input": {field: command}})
      for n, command in enumerate(commands)
    ]
    out = self.judge(self.spec(condition=condition, old=runs, new=group("new-run-", followed=10)))
    return {item["id"]: item["outcome"] for item in out["per_run"] if item["group"] == "old"}

  def test_root_search_predicate_outcomes(self):
    cases = {
      'command grep -rn "x" ~/.claude': "violated",
      'rg -n "x" ~': "violated",
      'find /Users/someone/Desktop/projects -name "*.md"': "violated",
      'cd /tmp && grep -R needle $HOME': "violated",
      'command grep -rn "x" ~/.claude --exclude-dir={.git,projects,cache}': "followed",
      'rg needle ~ -g "!projects"': "followed",
      'find ~/.claude -path "*/projects" -prune -o -name "*.md" -print': "followed",
      'command grep -rn "x" ~/.claude/memory': "no_opportunity",
      'grep "x" ~/.claude/settings.json': "no_opportunity",
      "ls ~/.claude": "no_opportunity",
    }
    commands = list(cases)
    got = self.per_run_outcomes(PREDICATE, commands)
    self.assertEqual([got["old-run-" + str(n)] for n in range(len(commands))], list(cases.values()))

  def test_stale_index_predicate_outcomes(self):
    cases = {
      "await tab.reload();\nawait tab.click(5);": "violated",
      "await tab.goto('https://example.test');\nawait tab.setValue(3, 'a');": "violated",
      "await tab.setValue(3, 'a');\nawait tab.click(5);": "violated",
      "await tab.reload();\nawait tab.getAXState();\nawait tab.click(5);": "followed",
      "await tab.getAXState();": "followed",
      "await tab.goto('https://example.test');": "no_opportunity",
    }
    commands = list(cases)
    got = self.per_run_outcomes(
      "predicate:stale-element-index@gate-2026-11-06-v1", commands, tool="mcp__lcu__js", field="code"
    )
    self.assertEqual([got["old-run-" + str(n)] for n in range(len(commands))], list(cases.values()))
    # 工具不是這個判斷式管的，就是無適用機會，不會被當成違反。
    other = self.per_run_outcomes(
      "predicate:stale-element-index@gate-2026-11-06-v1", ["await tab.reload(); await tab.click(5);"]
    )
    self.assertEqual(other, {"old-run-0": "no_opportunity"})

  def test_root_search_in_a_later_chained_segment_still_counts_as_a_violation(self):
    chained = COMPLIANT + ' && command grep -rn "needle" ~/.claude'
    swapped = VIOLATING + " ; " + COMPLIANT
    both_excluded = COMPLIANT + " && " + COMPLIANT
    got = self.per_run_outcomes(PREDICATE, [chained, swapped, both_excluded])
    self.assertEqual([got["old-run-" + str(n)] for n in range(3)], ["violated", "violated", "followed"])
    # 整場判定：新規則組全都帶著未排除的第二段，不能因為第一段有排除就算有效。
    out = self.judge(self.spec(
      old=group("old-run-", violated=6, followed=4),
      new=[make_run("new-run-" + str(n), bash(chained)) for n in range(10)],
    ))
    self.assertEqual(out["new"]["violated"], 10)
    self.assertEqual(out["verdict"], "ineffective")

  # ---- 第一層沒有跑任何測試不能判有效；第四層沒有事件不能判無效 ------------------------------

  def test_layer_one_with_zero_test_cases_is_insufficient_not_effective(self):
    out = self.judge(self.spec(level=1, tests={"cases": 0, "failed": 0}), apply=True)
    self.assertEqual((out["verdict"], out["evidence_scope"]), ("insufficient", None))
    self.assertEqual(self.rows()[-1]["verdict"], "insufficient")
    # 對照：有跑 1 個案例且全過，仍然判有效。
    ran = self.judge(self.spec(level=1, tests={"cases": 1, "failed": 0}))
    self.assertEqual(ran["verdict"], "effective")

  def test_layer_four_with_no_events_is_insufficient_not_ineffective(self):
    out = self.judge(self.spec(level=4, events=[]), apply=True)
    self.assertEqual(out["verdict"], "insufficient")
    self.assertEqual(self.rows()[-1]["verdict"], "insufficient")
    # 對照：只有 1 個事件且明確沒擋住，仍然判無效。
    one = self.judge(self.spec(level=4, events=[{"id": "event-0", "blocked": False}]))
    self.assertEqual(one["verdict"], "ineffective")

  # ---- 端點回跨來源重新導向時，不跟隨、不把金鑰送走 -------------------------------------------

  def test_model_endpoint_redirect_is_not_followed_and_is_a_model_failure(self):
    elsewhere = self.fake()
    redirecting = self.fake(lambda _i, _raw: (302, {"location": elsewhere.url}, 0))
    spec = self.spec(
      condition=MODEL_CONDITION,
      old=group("old-run-", violated=6, followed=4, marked=True),
      new=group("new-run-", violated=2, followed=8, marked=True),
    )
    error = self.judge(spec, model=redirecting, apply=True, code=3, env={"RETRO_JUDGE_API_KEY": "synthetic-judge-key-1f3a"})
    self.assertEqual(error["failure"], "http_error")
    self.assertEqual(len(redirecting.requests), 1)
    self.assertEqual(len(elsewhere.requests), 0)
    self.assertEqual(self.rows(), [])

  def test_unknown_predicate_or_version_is_refused_instead_of_asking_the_model(self):
    model = self.fake()
    for condition, reason in (
      ("predicate:no-such-check@gate-2026-11-06-v1", "unknown predicate"),
      ("predicate:root-search-without-exclude@gate-2099-01-01-v9", "version is not supported"),
    ):
      with self.subTest(condition=condition):
        error = self.judge(self.spec(condition=condition), model=model, apply=True, code=2)
        self.assertIn(reason, error["error"])
    self.assertEqual(model.requests, [])
    self.assertEqual(self.rows(), [])

  def test_input_missing_a_required_field_is_refused_naming_the_field(self):
    for field in ("change_id", "level", "evidence", "condition", "runs"):
      with self.subTest(field=field):
        spec = self.spec()
        del spec[field]
        error = self.judge(spec, apply=True, code=2)
        self.assertIn(field, error["error"])
    wrong = self.judge({**self.spec(), "runs": {"old": "nope", "new": []}}, apply=True, code=2)
    self.assertIn("runs.old", wrong["error"])
    self.assertEqual(self.rows(), [])

  def test_a_text_condition_without_a_model_endpoint_produces_no_verdict(self):
    error = self.judge(self.spec(condition=MODEL_CONDITION), apply=True, code=2)
    self.assertIn("needs a model", error["error"])
    self.assertEqual(self.rows(), [])

  # ---- 寫進累積庫的判定紀錄 -----------------------------------------------------------------

  def test_verdict_record_is_written_through_retro_store_only_when_applied(self):
    spec = self.spec()
    dry = self.judge(spec)
    self.assertEqual((dry["apply"], dry["store"]["appended"]), (False, 1))
    self.assertEqual(self.rows(), [])
    done = self.judge(spec, apply=True)
    self.assertEqual((done["apply"], done["store"]["appended"]), (True, 1))
    rows = self.rows()
    self.assertEqual(len(rows), 1)
    row = rows[0]
    self.assertEqual(row["id"], done["store"]["id"])
    self.assertEqual(
      {key: row[key] for key in ("issue_id", "change_id", "verdict", "level", "model", "route", "runs",
                                 "old_recurrences", "new_recurrences", "judged_by", "side_effects", "at")},
      {"issue_id": "issue-1", "change_id": self.change, "verdict": "effective", "level": 2,
       "model": "model-x", "route": "relay-main", "runs": 10, "old_recurrences": 6,
       "new_recurrences": 2, "judged_by": "main", "side_effects": "unchecked", "at": JUDGED_AT},
    )
    self.assertEqual(row["source"], spec["evidence"])
    # 同一份輸入重跑不會多寫一筆。
    again = self.judge(spec, apply=True)
    self.assertEqual((again["store"]["appended"], again["store"]["noop"]), (0, 1))
    self.assertEqual(len(self.rows()), 1)
    # 判定出來了，每個改動的 effective 才有值。
    views = self.store_cli("list", "--issue", "issue-1")["issues"][0]["changes"]
    self.assertEqual(next(v for v in views if v["id"] == self.change)["effective"], True)

  def test_store_refusals_surface_and_write_nothing(self):
    unknown = self.judge({**self.spec(), "change_id": "no-such-change"}, apply=True, code=2)
    self.assertIn("unknown change", unknown["error"])
    bad_quote = self.spec()
    bad_quote["evidence"] = {**bad_quote["evidence"], "quote": "not on that line"}
    refused = self.judge(bad_quote, apply=True, code=2)
    self.assertIn("quote is not on the source line", refused["error"])
    self.assertEqual(self.rows(), [])


if __name__ == "__main__":
  unittest.main()
