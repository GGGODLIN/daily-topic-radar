#!/usr/bin/env python3
"""session-audit 的規則違反只讀查詢，以及寫入摩擦檔前的管道分工。

跑法：python3 scripts/local-analysis/session-audit-rule-query.test.py
資料庫 schema 與規則身分由 session-audit 自己產生（見 session-audit-rule-fixture.py），列內容逐筆手寫。
不讀正式 state、摩擦檔或累積庫。
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
AUDIT = HERE / "session-audit.py"
STORE = HERE / "retro-store.py"

_spec = importlib.util.spec_from_file_location("rule_fixture", HERE / "session-audit-rule-fixture.py")
fixture_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture_module)

EFFECTIVE = "2026-10-10T00:00:00Z"

RULES = {
  "CLAUDE.md": (
    "# 全域設定\n\n## 程式碼風格\n\n"
    "- ALPHA_RULE 使用 ?? 而非 || 做 value fallback\n"
    "- BETA_RULE 使用 2 空格縮排\n"
  ),
  "rules/common/search.md": "# Search\n\n- GAMMA_RULE 大目錄搜尋要排除目錄\n",
}


def epoch(stamp):
  return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


def stamps(day, count):
  # 同一天內每行相隔一小時。
  return [f"2026-10-{day:02d}T{hour:02d}:00:00Z" for hour in range(1, count + 1)]


class QueryBase(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.root = Path(self.tmp.name)
    self.fx = fixture_module.RuleFixture(self.root)
    self.c0 = self.fx.commit_rules(RULES, "2026-09-01T00:00:00+00:00")
    self.env = os.environ.copy()
    self.env["HOME"] = str(self.root / "home")

  def audit(self, *args, code=0):
    done = subprocess.run(
      [sys.executable, str(AUDIT), *map(str, args)], capture_output=True, text=True, env=self.env
    )
    self.assertEqual(done.returncode, code, done.stdout + done.stderr)
    return done

  def query(self, path, quote, since=EFFECTIVE, code=0):
    done = self.audit(
      "rule-violations", "--state", self.fx.state, "--rules-repo", self.fx.rules_repo,
      "--rule-path", path, "--rule-quote", quote, "--since", since, code=code,
    )
    return json.loads(done.stdout) if code == 0 else done.stderr


ALPHA_QUOTE = "ALPHA_RULE 使用 ?? 而非 || 做 value fallback"
BETA_QUOTE = "BETA_RULE 使用 2 空格縮排"


class RuleQueryTest(QueryBase):
  def test_counts_distinct_sessions_with_pointers_and_coverage(self):
    fx, c0 = self.fx, self.c0
    fx.add_session("s1", stamps(11, 2), c0, [("ALPHA_RULE", 2, "ALPHA_VIOL_S1")], mtime=epoch("2026-10-11T03:00:00Z"))
    fx.add_session("s2", stamps(12, 3), c0, [("ALPHA_RULE", 1, "ALPHA_VIOL_S2A"), ("ALPHA_RULE", 3, "ALPHA_VIOL_S2B")], mtime=epoch("2026-10-12T05:00:00Z"))
    fx.add_session("s3", stamps(13, 1), c0, [("BETA_RULE", 1, "BETA_VIOL_S3")], mtime=epoch("2026-10-13T02:00:00Z"))
    fx.add_session("s4", stamps(14, 1), c0, [], mtime=epoch("2026-10-14T02:00:00Z"))
    # 絕對路徑（累積庫的錨點長這樣）與相對路徑要得到同一個答案。
    out = self.query(str(fx.rules_repo / "CLAUDE.md"), ALPHA_QUOTE)
    self.assertEqual(out["command"], "rule-violations")
    self.assertTrue(out["found"])
    self.assertEqual(out["status"], "analyzed")
    self.assertEqual(out["violating_sessions"], 2)
    pointers = [(item["session_id"], item["line"], item["event_time"], item["quote"]) for item in out["violations"]]
    self.assertEqual(
      pointers,
      [
        ("s1", 2, "2026-10-11T02:00:00Z", "ALPHA_VIOL_S1"),
        ("s2", 1, "2026-10-12T01:00:00Z", "ALPHA_VIOL_S2A"),
        ("s2", 3, "2026-10-12T03:00:00Z", "ALPHA_VIOL_S2B"),
      ],
    )
    for item in out["violations"]:
      self.assertRegex(item["source_ref"], rf"^session:{item['session_id']}#{item['line']}:[0-9a-f]{{16}}$")
    self.assertEqual(out["coverage"], {"sessions": 4, "latest": "2026-10-14T02:00:00Z"})
    again = self.query("CLAUDE.md", ALPHA_QUOTE)
    self.assertEqual(again, out)

  def test_other_rules_under_the_same_heading_are_not_counted(self):
    fx, c0 = self.fx, self.c0
    fx.add_session("a1", stamps(11, 1), c0, [("ALPHA_RULE", 1, "ALPHA_VIOL")], mtime=epoch("2026-10-11T03:00:00Z"))
    fx.add_session("b1", stamps(12, 1), c0, [("BETA_RULE", 1, "BETA_VIOL_1")], mtime=epoch("2026-10-12T03:00:00Z"))
    fx.add_session("b2", stamps(13, 1), c0, [("BETA_RULE", 1, "BETA_VIOL_2")], mtime=epoch("2026-10-13T03:00:00Z"))
    alpha = self.query("CLAUDE.md", ALPHA_QUOTE)
    beta = self.query("CLAUDE.md", BETA_QUOTE)
    self.assertEqual([item["quote"] for item in alpha["violations"]], ["ALPHA_VIOL"])
    # 對照：同一條查詢對另一條規則，回的是另一條規則自己的違反。
    self.assertEqual([item["quote"] for item in beta["violations"]], ["BETA_VIOL_1", "BETA_VIOL_2"])
    self.assertEqual((alpha["violating_sessions"], beta["violating_sessions"]), (1, 2))

  def test_filters_by_each_violation_event_time_not_session_start(self):
    fx, c0 = self.fx, self.c0
    # 長 session：從生效前開始，第 2 行（生效前）與第 4 行（生效後）各違反一次，只該算第 4 行。
    fx.add_session(
      "long", ["2026-09-30T01:00:00Z", "2026-10-05T01:00:00Z", "2026-10-12T01:00:00Z", "2026-10-15T01:00:00Z"], c0,
      [("ALPHA_RULE", 2, "ALPHA_BEFORE"), ("ALPHA_RULE", 4, "ALPHA_AFTER")], mtime=epoch("2026-10-15T02:00:00Z"),
    )
    # 整場都在生效前：不算，也不算覆蓋。
    fx.add_session("early", ["2026-10-01T01:00:00Z", "2026-10-02T01:00:00Z"], c0, [("ALPHA_RULE", 1, "ALPHA_EARLY")], mtime=epoch("2026-10-03T00:00:00Z"))
    # 對照：生效後才開始的 session 照算。
    fx.add_session("late", stamps(16, 1), c0, [("ALPHA_RULE", 1, "ALPHA_LATE")], mtime=epoch("2026-10-16T02:00:00Z"))
    out = self.query("CLAUDE.md", ALPHA_QUOTE)
    self.assertEqual([item["quote"] for item in out["violations"]], ["ALPHA_AFTER", "ALPHA_LATE"])
    self.assertEqual(out["violating_sessions"], 2)
    self.assertEqual(out["coverage"]["sessions"], 2)
    # 起始時間移到 10-14：長 session 第 4 行仍在，生效後的 late 也在；移到 10-16T00：只剩 late。
    self.assertEqual(self.query("CLAUDE.md", ALPHA_QUOTE, since="2026-10-16T00:00:00Z")["violating_sessions"], 1)

  def test_zero_coverage_says_not_analyzed_instead_of_zero_violations(self):
    fx, c0 = self.fx, self.c0
    fx.add_session("s1", stamps(11, 1), c0, [], mtime=epoch("2026-10-11T03:00:00Z"))
    silent = self.query("CLAUDE.md", ALPHA_QUOTE, since="2027-01-01T00:00:00Z")
    self.assertTrue(silent["found"])
    self.assertEqual(silent["status"], "not_analyzed")
    self.assertEqual(silent["label"], "尚未分析")
    self.assertIsNone(silent["violating_sessions"])
    self.assertEqual(silent["coverage"], {"sessions": 0, "latest": None})
    # 對照：有覆蓋、沒有違反，是真的零違反。
    covered = self.query("CLAUDE.md", ALPHA_QUOTE)
    self.assertEqual(covered["status"], "analyzed")
    self.assertEqual(covered["violating_sessions"], 0)
    self.assertEqual(covered["violations"], [])
    self.assertEqual(covered["coverage"]["sessions"], 1)
  def test_sessions_that_loaded_a_version_without_the_paragraph_are_not_coverage(self):
    fx = self.fx
    old = fx.commit_rules({"CLAUDE.md": "# 全域設定\n\n## 程式碼風格\n\n- OLD_RULE 舊規則\n"}, "2026-09-02T00:00:00+00:00")
    new = fx.commit_rules(RULES, "2026-10-09T00:00:00+00:00")
    fx.add_session("old-version", stamps(11, 1), old, [], mtime=epoch("2026-10-11T03:00:00Z"))
    fx.add_session("new-version", stamps(12, 1), new, [], mtime=epoch("2026-10-12T03:00:00Z"))
    out = self.query("CLAUDE.md", ALPHA_QUOTE)
    self.assertEqual(out["coverage"], {"sessions": 1, "latest": "2026-10-12T03:00:00Z"})
    self.assertEqual(out["violating_sessions"], 0)

  def test_paragraph_edited_since_reports_not_found_not_zero(self):
    fx, c0 = self.fx, self.c0
    fx.add_session("s1", stamps(11, 1), c0, [("ALPHA_RULE", 1, "ALPHA_VIOL")], mtime=epoch("2026-10-11T03:00:00Z"))
    edited = RULES["CLAUDE.md"].replace("使用 ?? 而非 ||", "一律使用 ?? 取代 ||")
    fx.commit_rules({"CLAUDE.md": edited}, "2026-10-12T00:00:00+00:00")
    gone = self.query("CLAUDE.md", ALPHA_QUOTE)
    self.assertFalse(gone["found"])
    self.assertEqual(gone["reason"], "rule-not-found")
    self.assertNotIn("violating_sessions", gone)
    self.assertNotIn("violations", gone)
    # 對照：沒被改的 BETA 在同一個 repo 查得到。
    fx.add_session("s2", stamps(13, 1), c0, [("BETA_RULE", 1, "BETA_VIOL")], mtime=epoch("2026-10-13T03:00:00Z"))
    kept = self.query("CLAUDE.md", BETA_QUOTE)
    self.assertTrue(kept["found"])
    self.assertEqual(kept["violating_sessions"], 1)
    # 路徑不對也是找不到，不是零。
    self.assertEqual(self.query("rules/common/search.md", BETA_QUOTE)["reason"], "rule-not-found")

  def test_output_never_carries_the_audit_rule_identity(self):
    fx, c0 = self.fx, self.c0
    fx.add_session("s1", stamps(11, 1), c0, [("ALPHA_RULE", 1, "ALPHA_VIOL")], mtime=epoch("2026-10-11T03:00:00Z"))
    rule_id = fx.rule_at(c0, "ALPHA_RULE")["id"]
    done = self.audit(
      "rule-violations", "--state", fx.state, "--rules-repo", fx.rules_repo,
      "--rule-path", "CLAUDE.md", "--rule-quote", ALPHA_QUOTE, "--since", EFFECTIVE,
    )
    self.assertFalse(rule_id in done.stdout, "輸出不帶 session-audit 的規則 id")
    self.assertTrue("ALPHA_VIOL" in done.stdout, "對照：輸出確實帶了這筆違反")

  def test_violation_whose_event_time_cannot_be_read_is_counted_apart_not_by_session_start(self):
    fx, c0 = self.fx, self.c0
    gone = fx.add_session("gone", stamps(12, 1), c0, [("ALPHA_RULE", 1, "ALPHA_GONE")], mtime=epoch("2026-10-12T03:00:00Z"))
    fx.add_session("here", stamps(13, 1), c0, [("ALPHA_RULE", 1, "ALPHA_HERE")], mtime=epoch("2026-10-13T03:00:00Z"))
    gone.unlink()
    out = self.query("CLAUDE.md", ALPHA_QUOTE)
    self.assertEqual([item["quote"] for item in out["violations"]], ["ALPHA_HERE"])
    self.assertEqual((out["violating_sessions"], out["unresolved_time"]), (1, 1))

  def test_quote_matching_two_rules_is_ambiguous_not_a_guess(self):
    out = self.query("CLAUDE.md", "使用")
    self.assertFalse(out["found"])
    self.assertEqual(out["reason"], "rule-ambiguous")
    # 對照：能唯一對上的原文照常解析。
    self.assertTrue(self.query("CLAUDE.md", "ALPHA_RULE")["found"])


class StoreBase(QueryBase):
  """累積庫只透過 retro-store CLI 建；規則錨點用 backfill 補在改動上，路徑指向臨時規則 repo 的真檔。"""

  def setUp(self):
    super().setUp()
    self.store = self.root / "retro-store"
    self.serial = 0
    packet = self.write_json(
      "packet.json",
      {
        "schema_version": 1,
        "run": {"id": "run-1", "source": "fixture", "status": "complete", "cutoff": EFFECTIVE},
        "issues": [{"id": "issue-1", "title": "Rules are ignored", "related_to": []}],
        "discoveries": [],
        "events": [],
      },
    )
    self.retro("ingest", "--packet", packet, "--apply")
    self.retro(
      "decide", "--issue", "issue-1", "--action", "approve", "--source", self.human("approve it", "2026-10-09T00:00:00Z"),
      "--line", 1, "--quote", "approve it", "--apply",
    )

  def write_json(self, name, value):
    path = self.root / name
    path.write_text(json.dumps(value, ensure_ascii=False))
    return path

  def human(self, text, at):
    self.serial += 1
    path = self.root / f"human-{self.serial}.jsonl"
    path.write_text(json.dumps({
      "type": "user", "timestamp": at, "uuid": f"human-{self.serial}",
      "message": {"role": "user", "content": text},
    }, ensure_ascii=False) + "\n")
    return path

  def retro(self, *args, code=0):
    done = subprocess.run(
      [sys.executable, "-I", str(STORE), "--store", str(self.store), *map(str, args)], capture_output=True, text=True
    )
    self.assertEqual(done.returncode, code, done.stdout + done.stderr)
    return json.loads(done.stdout if code == 0 else done.stderr)

  def anchored_change(self, change_id, path, marker, effective_at=EFFECTIVE, kind="always_on_rule"):
    note = self.root / f"{change_id}.txt"
    note.write_text(f"condition for {change_id}\n")
    self.retro(
      "change", "--issue", "issue-1", "--id", change_id, "--effective-at", effective_at, "--source", note,
      "--line", 1, "--quote", f"condition for {change_id}", "--condition", "kept", "--apply",
    )
    full = self.fx.rules_repo / path
    line = self.fx.line_of(path, marker)
    quote = full.read_text().splitlines()[line - 1].lstrip("- ").strip()
    self.retro(
      "backfill", "--change", change_id, "--kind", kind, "--source", full, "--line", line, "--quote", quote, "--apply",
    )
    return {"path": str(full), "line": line, "quote": quote}

  def summary(self):
    return self.retro(
      "list", "--summary", "--audit-state", self.fx.state, "--rules-repo", self.fx.rules_repo
    )["pending_decisions"]

  def hooked(self):
    return {item["rule"]["quote"]: item for item in self.summary()["make_hook"]}

  def violate(self, rule_marker, names, day, quote_prefix=None):
    # 每個名稱一場，各自只在 day 這天違反一次；回傳該規則的所有引文。
    quotes = []
    for index, name in enumerate(names):
      quote = f"{quote_prefix or rule_marker}_{name}"
      self.fx.add_session(
        name, stamps(day, 2), self.c0, [(rule_marker, 1, quote)], mtime=epoch(f"2026-10-{day:02d}T05:00:00Z")
      )
      quotes.append(quote)
    return quotes


class MakeHookProposalTest(StoreBase):
  def test_three_sessions_after_the_change_propose_a_hook_and_two_do_not(self):
    alpha = self.anchored_change("c-alpha", "CLAUDE.md", "ALPHA_RULE")
    beta = self.anchored_change("c-beta", "CLAUDE.md", "BETA_RULE")
    alpha_quotes = self.violate("ALPHA_RULE", ["a1", "a2", "a3"], 12)
    self.violate("BETA_RULE", ["b1", "b2"], 13)
    before = (self.store / "records.jsonl").read_bytes()
    listed = self.hooked()
    self.assertEqual(set(listed), {alpha["quote"]})
    item = listed[alpha["quote"]]
    self.assertEqual(item["rule"], {"path": alpha["path"], "quote": alpha["quote"]})
    self.assertEqual(item["change_id"], "c-alpha")
    self.assertEqual((item["violating_sessions"], item["since"]), (3, EFFECTIVE))
    pointers = [entry["source_ref"] for entry in item["evidence"]]
    self.assertGreaterEqual(len(pointers), 3)
    for quote in alpha_quotes:
      self.assertTrue(any(entry["quote"] == quote for entry in item["evidence"]), quote)
    for entry in item["evidence"]:
      self.assertRegex(entry["source_ref"], r"^session:a[123]#1:[0-9a-f]{16}$")
    # 只提出，不自動建立任何東西：累積庫位元組不變，也沒有提議決策。
    self.assertEqual((self.store / "records.jsonl").read_bytes(), before)
    self.assertEqual([row for row in self.rows() if row["type"] == "proposal_decision"], [])
    # 對照：第 3 場進來，BETA 才被提出。
    self.violate("BETA_RULE", ["b3"], 14)
    self.assertEqual(set(self.hooked()), {alpha["quote"], beta["quote"]})

  def rows(self):
    return [json.loads(line) for line in (self.store / "records.jsonl").read_text().splitlines() if line.strip()]

  def test_rejection_restarts_the_count_from_the_rejection_time(self):
    alpha = self.anchored_change("c-alpha", "CLAUDE.md", "ALPHA_RULE")
    self.violate("ALPHA_RULE", ["a1", "a2", "a3"], 12)
    self.assertEqual(set(self.hooked()), {alpha["quote"]})
    rejected_at = "2026-10-14T00:00:00Z"
    self.retro(
      "proposal", "--kind", "make_hook", "--decision", "rejected", "--rule-path", alpha["path"],
      "--rule-line", alpha["line"], "--rule-quote", alpha["quote"],
      "--source", self.human("no hook for that", rejected_at), "--line", 1, "--quote", "no hook for that", "--apply",
    )
    self.assertEqual(self.hooked(), {}, "拒絕前的 3 場不再算")
    # 拒絕之後只有 2 場（其中一場是拒絕前就開始的長 session，違反發生在拒絕之後）。
    self.violate("ALPHA_RULE", ["a4"], 15)
    self.fx.add_session(
      "long", ["2026-10-13T01:00:00Z", "2026-10-16T01:00:00Z"], self.c0, [("ALPHA_RULE", 2, "ALPHA_AFTER_REJECT")],
      mtime=epoch("2026-10-16T02:00:00Z"),
    )
    self.assertEqual(self.hooked(), {}, "拒絕後 2 場不提")
    # 第 3 場進來：從拒絕時間重算，剛好滿 3。
    self.fx.add_session("a5", stamps(17, 2), self.c0, [("ALPHA_RULE", 1, "ALPHA_A5")], mtime=epoch("2026-10-17T05:00:00Z"))
    again = self.hooked()
    self.assertEqual(set(again), {alpha["quote"]})
    self.assertEqual((again[alpha["quote"]]["violating_sessions"], again[alpha["quote"]]["since"]), (3, rejected_at))

  def test_a_hook_proposal_the_user_accepted_leaves_the_list(self):
    alpha = self.anchored_change("c-alpha", "CLAUDE.md", "ALPHA_RULE")
    self.violate("ALPHA_RULE", ["a1", "a2", "a3"], 12)
    self.assertEqual(set(self.hooked()), {alpha["quote"]})
    self.retro(
      "proposal", "--kind", "make_hook", "--decision", "accepted", "--rule-path", alpha["path"],
      "--rule-line", alpha["line"], "--rule-quote", alpha["quote"],
      "--source", self.human("yes make the hook", "2026-10-14T00:00:00Z"), "--line", 1, "--quote", "yes make the hook", "--apply",
    )
    self.assertEqual(self.hooked(), {})

  def test_without_audit_flags_the_list_says_the_query_did_not_run(self):
    self.anchored_change("c-alpha", "CLAUDE.md", "ALPHA_RULE")
    plain = self.retro("list", "--summary")["pending_decisions"]
    self.assertEqual(plain["make_hook"], [])
    self.assertEqual(plain["make_hook_unchecked"], [{"reason": "audit-state-not-given"}])
    # 對照：給了旗標且有分析過的 session，這個欄位變成空清單（沒有查不到的項目）。
    self.fx.add_session("s1", stamps(11, 1), self.c0, [], mtime=epoch("2026-10-11T03:00:00Z"))
    self.assertEqual(self.summary()["make_hook_unchecked"], [])

  def test_rule_that_cannot_be_resolved_or_has_no_coverage_is_listed_as_unchecked_not_dropped(self):
    gone = self.anchored_change("c-beta", "CLAUDE.md", "BETA_RULE")
    gamma = self.anchored_change("c-gamma", "rules/common/search.md", "GAMMA_RULE")
    # BETA 之後被改寫；GAMMA 還沒有任何 session 分析過。
    self.fx.commit_rules({"CLAUDE.md": RULES["CLAUDE.md"].replace("使用 2 空格縮排", "一律縮排 2 格")}, "2026-10-12T00:00:00+00:00")
    unchecked = {item["rule"]["quote"]: item["reason"] for item in self.summary()["make_hook_unchecked"]}
    self.assertEqual(unchecked, {gone["quote"]: "rule-not-found", gamma["quote"]: "not-analyzed"})
    self.assertEqual(self.summary()["make_hook"], [])
    # 對照：GAMMA 有 session 分析過、只是沒違反，就不再是未查項目，也不是提議。
    self.fx.add_session("s1", stamps(13, 1), self.c0, [], mtime=epoch("2026-10-13T03:00:00Z"))
    after = {item["rule"]["quote"]: item["reason"] for item in self.summary()["make_hook_unchecked"]}
    self.assertEqual(after, {gone["quote"]: "rule-not-found"})
    self.assertEqual(self.summary()["make_hook"], [])


class FrictionRoutingTest(StoreBase):
  FRICTION = "# 摩擦\n\n## 待折\n\n- 2026-01-01 old pending item\n\n## 休眠\n\n## 已折／已否決\n\n- 2026-01-02 old closed item\n"

  def promote(self, store=None):
    friction = self.root / "friction"
    friction.mkdir(exist_ok=True)
    target = friction / "workflow-general.md"
    if not target.exists():
      target.write_text(self.FRICTION)
    done = self.audit(
      "promote", "--state", self.fx.state, "--friction-root", friction, "--rules-repo", self.fx.rules_repo,
      "--retro-store", store or self.store,
    )
    return json.loads(done.stdout), target.read_text()

  def seed(self):
    # 同一標題下兩條規則各 3 場違反；只有 ALPHA 做過改動。
    self.violate("ALPHA_RULE", ["a1", "a2", "a3"], 12)
    self.violate("BETA_RULE", ["b1", "b2", "b3"], 13)

  def test_rules_with_a_change_anchor_are_skipped_and_others_are_written(self):
    alpha = self.anchored_change("c-alpha", "CLAUDE.md", "ALPHA_RULE")
    self.seed()
    out, text = self.promote()
    self.assertEqual((out["rule_entries"], out["rule_sublines"]), (1, 3), out)
    self.assertTrue("BETA_RULE" in text)
    self.assertTrue("BETA_RULE_b1" in text)
    self.assertFalse("ALPHA_RULE" in text, "有改動錨點的規則不進摩擦檔")
    # 同一條規則不會同時在兩邊：它在待決定清單。
    self.assertEqual(set(self.hooked()), {alpha["quote"]})
    # 對照：沒有任何改動錨點時，同一份資料兩條規則都寫進去。
    fresh = self.root / "empty-store"
    friction_before = self.root / "friction" / "workflow-general.md"
    friction_before.write_text(self.FRICTION)
    out2, text2 = self.promote(store=fresh)
    self.assertEqual((out2["rule_entries"], out2["rule_sublines"]), (2, 6), out2)
    self.assertTrue("ALPHA_RULE" in text2 and "BETA_RULE" in text2)

  def test_existing_friction_entries_stay_put_and_get_no_new_sublines_for_skipped_rules(self):
    self.anchored_change("c-alpha", "CLAUDE.md", "ALPHA_RULE")
    self.seed()
    first, _ = self.promote(store=self.root / "empty-store")
    self.assertEqual(first["rule_entries"], 2)
    friction = self.root / "friction" / "workflow-general.md"
    before = friction.read_text()
    self.violate("ALPHA_RULE", ["a4"], 14)
    self.violate("BETA_RULE", ["b4"], 14)
    out, after = self.promote()
    # ALPHA 現在有改動錨點：不再追加，舊條目與舊子行原樣留著；BETA 照常追加一條子行。
    self.assertEqual((out["rule_entries"], out["rule_sublines"]), (0, 1), out)
    old = before.splitlines()
    cursor = 0
    for line in after.splitlines():
      if cursor < len(old) and line == old[cursor]:
        cursor += 1
    self.assertEqual(cursor, len(old), "舊行都還在且順序不變")
    self.assertTrue("ALPHA_RULE_a1" in after and not "ALPHA_RULE_a4" in after)
    self.assertTrue("BETA_RULE_b4" in after)

  def test_unreadable_store_withholds_rule_entries_instead_of_writing_them_twice(self):
    self.anchored_change("c-alpha", "CLAUDE.md", "ALPHA_RULE")
    self.seed()
    (self.store / "records.jsonl").write_text("not json\n")
    out, text = self.promote()
    self.assertEqual(out["rules_withheld"], "retro-store-unreadable")
    self.assertFalse("ALPHA_RULE" in text or "BETA_RULE" in text)
    # 對照：庫正常時同一份資料會寫入規則條目。
    (self.store / "records.jsonl").unlink()
    out2, text2 = self.promote(store=self.root / "empty-store")
    self.assertNotIn("rules_withheld", out2)
    self.assertTrue("BETA_RULE" in text2)


if __name__ == "__main__":
  unittest.main()
