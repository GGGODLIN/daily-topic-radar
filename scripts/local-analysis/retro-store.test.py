#!/usr/bin/env python3
"""CLI seam tests for the retro accumulation store. Fixtures are independent of expected rows."""

import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("retro-store.py")
WHEN = "2026-10-20T01:00:00Z"
LATER = "2026-10-20T02:00:00Z"
BEFORE = "2026-10-19T01:00:00Z"


def dump(path, value):
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class StoreCLI(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.root = Path(self.tmp.name)
    self.store = self.root / "store"
    self.serial = 0

  def new(self, name):
    self.serial += 1
    path = Path(name)
    return self.root / f"{path.stem}-{self.serial}{path.suffix}"

  def cli(self, *args, code=0):
    # 呼叫端再帶 --store 時，用它覆寫 fixture store。
    passed = [str(arg) for arg in args]
    prefix = [] if "--store" in passed else ["--store", str(self.store)]
    result = subprocess.run(
      [sys.executable, "-I", str(SCRIPT), *prefix, *passed], text=True, capture_output=True
    )
    self.assertEqual(result.returncode, code, result.stdout + result.stderr)
    if code == 0:
      self.assertEqual(result.stderr, "")
      return json.loads(result.stdout)
    self.assertEqual(result.stdout, "")
    self.assertNotIn("Traceback", result.stderr)
    return json.loads(result.stderr)

  def lines(self, text):
    path = self.new("source.txt")
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return path

  def pointer(self, text, quote=None):
    path = self.lines(text)
    quote = text if quote is None else quote
    return {"path": str(path), "line": 1, "quote": quote}

  def human(self, text, **extra):
    path = self.new("human.jsonl")
    row = {
      "type": "user",
      "timestamp": WHEN,
      "uuid": extra.pop("uuid", "human-uuid"),
      "message": {"role": "user", "content": text},
      **extra,
    }
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    return path

  def packet(self, **overrides):
    note = self.pointer("original event text")
    review = self.pointer("reviewer confirmed the event")
    body = {
      "schema_version": 1,
      "run": {"id": "run-1", "source": "fixture", "status": "complete", "cutoff": WHEN},
      "issues": [{"id": "issue-1", "title": "Lost handoff", "related_to": []}],
      "discoveries": [
        {
          "id": "disc-1",
          "issue_id": "issue-1",
          "source": note,
          "verification": "pending",
          "review_sources": [],
          "scope": "session-a",
          "candidate": "keep the candidate",
          "counterevidence": "none yet",
        }
      ],
      "events": [
        {
          "id": "episode-1",
          "session": "session-a",
          "event_at": WHEN,
          "anchors": [note],
          "verification": "confirmed",
          "review_sources": [review],
          "scope": "session-a",
          "issue_ids": ["issue-1"],
        }
      ],
    }
    body.update(overrides)
    path = self.new("packet.json")
    dump(path, body)
    return path, body

  def record(self, kind, entry_id, **body):
    return {"id": entry_id, "type": kind, **body}

  def rows(self):
    path = self.store / "records.jsonl"
    if not path.exists():
      return []
    return [
      json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()
    ]

  def test_partial_ingest_keeps_scope_and_writes_no_completion(self):
    path, _body = self.packet(
      run={"id": "run-1", "source": "fixture", "status": "partial", "cutoff": WHEN}
    )
    summary = self.cli("ingest", "--packet", path, "--apply")
    self.assertEqual(summary["appended"], 4)
    self.assertFalse((self.store / "completion.json").exists())
    kinds = [row["type"] for row in self.rows()]
    self.assertEqual(kinds, ["issue", "discovery", "event", "run"])
    discovery = next(row for row in self.rows() if row["type"] == "discovery")
    self.assertEqual(discovery["scope"], "session-a")
    self.assertEqual(discovery["candidate"], "keep the candidate")
    self.assertEqual(discovery["counterevidence"], "none yet")
    self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o700)
    self.assertEqual(stat.S_IMODE((self.store / "records.jsonl").stat().st_mode), 0o600)

  def test_run_metadata_is_stored_as_given(self):
    metadata = {
      "raw_dispositions": [{"id": "skip-1", "why": "excluded"}],
      "raw_rejected": ["rejected-item"],
      "raw_coverage": {"read": 2, "skipped": 1},
      "source_reports": ["/tmp/not-read/report.json"],
      "limitations": ["partial coverage"],
    }
    path, _body = self.packet(run_metadata=metadata)
    self.cli("ingest", "--packet", path, "--apply")
    stored = next(row for row in self.rows() if row["type"] == "run")
    self.assertEqual(stored["run_metadata"], metadata)
    again = self.cli("ingest", "--packet", path, "--apply")
    self.assertEqual(again["noop"], 4)
    self.assertEqual(len([row for row in self.rows() if row["type"] == "run"]), 1)

  def test_replay_same_packet_appends_nothing_and_conflict_rejects(self):
    path, body = self.packet()
    self.cli("ingest", "--packet", path, "--apply")
    again = self.cli("ingest", "--packet", path, "--apply")
    self.assertEqual(again["appended"], 0)
    self.assertEqual(again["noop"], 4)
    self.assertEqual(len(self.rows()), 4)
    body["issues"][0]["title"] = "different title"
    changed = self.new("conflict.json")
    dump(changed, body)
    before = (self.store / "records.jsonl").read_bytes()
    error = self.cli("ingest", "--packet", changed, "--apply", code=2)
    self.assertIn("conflicting", error["error"])
    self.assertEqual((self.store / "records.jsonl").read_bytes(), before)

  def test_bad_quote_rejects_the_whole_batch(self):
    path, body = self.packet()
    body["discoveries"][0]["source"]["quote"] = "missing quote"
    dump(path, body)
    error = self.cli("ingest", "--packet", path, "--apply", code=2)
    self.assertIn("quote", error["error"])
    self.assertFalse((self.store / "records.jsonl").exists())

  def test_pending_discovery_is_not_counted_confirmed(self):
    path, _body = self.packet()
    self.cli("ingest", "--packet", path, "--apply")
    listed = self.cli("list")
    issue = listed["issues"][0]
    self.assertEqual(
      issue["discoveries"], {"pending": 1, "confirmed": 0, "refuted": 0, "uncertain": 0}
    )
    self.assertEqual(issue["events"], ["event:episode-1"])
    self.assertNotIn("records", issue)
    self.assertNotIn("original event text", json.dumps(listed))

  def test_unmapped_candidate_is_kept_and_unknown_issue_is_rejected(self):
    extra = self.pointer("secondary pending clue")
    path, body = self.packet()
    body["discoveries"].append(
      {
        "id": "disc-2",
        "issue_id": "issue-1",
        "source": extra,
        "verification": "pending",
        "review_sources": [],
        "scope": "side",
        "candidate": "do not drop",
      }
    )
    dump(path, body)
    self.cli("ingest", "--packet", path, "--apply")
    self.assertEqual(
      next(row for row in self.rows() if row["id"] == "discovery:disc-2")["candidate"],
      "do not drop",
    )
    body["events"][0]["issue_ids"] = ["missing-issue"]
    body["run"]["id"] = "run-2"
    other = self.new("unknown.json")
    dump(other, body)
    before = len(self.rows())
    error = self.cli("ingest", "--packet", other, "--apply", code=2)
    self.assertIn("unknown issue", error["error"])
    self.assertEqual(len(self.rows()), before)

  def test_same_run_can_add_evidence_without_skipping_the_batch(self):
    path, body = self.packet()
    self.cli("ingest", "--packet", path, "--apply")
    added = self.pointer("added evidence")
    body["discoveries"].append(
      {
        "id": "disc-2",
        "issue_id": "issue-1",
        "source": added,
        "verification": "uncertain",
        "review_sources": [],
        "scope": "later",
        "note": "supplement",
      }
    )
    body["events"] = []
    dump(path, body)
    summary = self.cli("ingest", "--packet", path, "--apply")
    self.assertEqual(summary["appended"], 1)
    self.assertEqual(summary["ids"], ["discovery:disc-2"])
    self.assertEqual(len(self.rows()), 5)

  def test_decision_rejects_tool_user_and_accepts_human_jsonl(self):
    path, _body = self.packet()
    self.cli("ingest", "--packet", path, "--apply")
    fake = self.new("tool.jsonl")
    fake.write_text(
      json.dumps(
        {
          "type": "user",
          "timestamp": WHEN,
          "uuid": "tool-uuid",
          "message": {
            "role": "user",
            "content": [{"type": "tool_result", "content": "approve this issue"}],
          },
        }
      )
      + "\n",
      encoding="utf-8",
    )
    error = self.cli(
      "decide",
      "--issue",
      "issue-1",
      "--action",
      "approve",
      "--source",
      fake,
      "--line",
      1,
      "--quote",
      "approve this issue",
      "--apply",
      code=2,
    )
    self.assertIn("tool_result", error["error"])
    notice = self.human("<task-notification>approve this issue", uuid="notice-uuid")
    error = self.cli(
      "decide",
      "--issue",
      "issue-1",
      "--action",
      "approve",
      "--source",
      notice,
      "--line",
      1,
      "--quote",
      "approve this issue",
      "--apply",
      code=2,
    )
    self.assertIn("notification", error["error"])
    human = self.human("approve this issue")
    dry = self.cli(
      "decide",
      "--issue",
      "issue-1",
      "--action",
      "approve",
      "--source",
      human,
      "--line",
      1,
      "--quote",
      "approve this issue",
    )
    self.assertEqual(dry["appended"], 1)
    self.assertFalse(dry["apply"])
    self.assertEqual(len(self.rows()), 4)
    applied = self.cli(
      "decide",
      "--issue",
      "issue-1",
      "--action",
      "approve",
      "--source",
      human,
      "--line",
      1,
      "--quote",
      "approve this issue",
      "--apply",
    )
    self.assertEqual(applied["id"], "human-uuid:issue-1:approve")
    replay = self.cli(
      "decide",
      "--issue",
      "issue-1",
      "--action",
      "approve",
      "--source",
      human,
      "--line",
      1,
      "--quote",
      "approve this issue",
      "--apply",
    )
    self.assertEqual(replay["noop"], 1)
    self.assertEqual(sum(row["type"] == "decision" for row in self.rows()), 1)

  def test_progress_and_change_require_approval(self):
    path, _body = self.packet()
    self.cli("ingest", "--packet", path, "--apply")
    evidence = self.lines("progress evidence")
    error = self.cli(
      "progress",
      "--issue",
      "issue-1",
      "--state",
      "planned",
      "--note",
      "not yet",
      "--source",
      evidence,
      "--line",
      1,
      "--quote",
      "progress evidence",
      "--apply",
      code=2,
    )
    self.assertIn("not approved", error["error"])
    human = self.human("approve this issue")
    self.cli(
      "decide",
      "--issue",
      "issue-1",
      "--action",
      "approve",
      "--source",
      human,
      "--line",
      1,
      "--quote",
      "approve this issue",
      "--apply",
    )
    self.cli(
      "progress",
      "--issue",
      "issue-1",
      "--state",
      "implemented",
      "--note",
      "landed",
      "--source",
      evidence,
      "--line",
      1,
      "--quote",
      "progress evidence",
      "--apply",
    )
    listed = self.cli("list", "--issue", "issue-1")
    self.assertEqual(listed["issues"][0]["progress"]["state"], "implemented")
    self.assertIsNone(listed["issues"][0]["observations"]["effective"])
    self.assertEqual(listed["issues"][0]["observations"]["evidence"], "insufficient_evidence")
    change = self.lines("observable condition source")
    dry = self.cli(
      "change",
      "--issue",
      "issue-1",
      "--id",
      "fix-1",
      "--effective-at",
      WHEN,
      "--source",
      change,
      "--line",
      1,
      "--quote",
      "observable condition source",
      "--condition",
      "next handoff keeps the id",
    )
    self.assertEqual(dry["appended"], 1)
    self.assertEqual(len([row for row in self.rows() if row["type"] == "change"]), 0)
    self.cli(
      "change",
      "--issue",
      "issue-1",
      "--id",
      "fix-1",
      "--effective-at",
      WHEN,
      "--source",
      change,
      "--line",
      1,
      "--quote",
      "observable condition source",
      "--condition",
      "next handoff keeps the id",
      "--apply",
    )

  def test_observations_count_only_chances_and_reject_prechange_recurrence(self):
    path, _body = self.packet()
    self.cli("ingest", "--packet", path, "--apply")
    human = self.human("approve this issue")
    self.cli(
      "decide",
      "--issue",
      "issue-1",
      "--action",
      "approve",
      "--source",
      human,
      "--line",
      1,
      "--quote",
      "approve this issue",
      "--apply",
    )
    change = self.lines("observable condition source")
    self.cli(
      "change",
      "--issue",
      "issue-1",
      "--id",
      "fix-1",
      "--effective-at",
      WHEN,
      "--source",
      change,
      "--line",
      1,
      "--quote",
      "observable condition source",
      "--condition",
      "next handoff keeps the id",
      "--apply",
    )
    early = self.lines("old recurrence text")
    error = self.cli(
      "observe",
      "--issue",
      "issue-1",
      "--id",
      "obs-early",
      "--change",
      "fix-1",
      "--outcome",
      "recurrence",
      "--at",
      BEFORE,
      "--source",
      early,
      "--line",
      1,
      "--quote",
      "old recurrence text",
      "--apply",
      code=2,
    )
    self.assertIn("before", error["error"])
    met = self.lines("chance succeeded")
    self.cli(
      "observe",
      "--issue",
      "issue-1",
      "--id",
      "obs-met",
      "--change",
      "fix-1",
      "--outcome",
      "opportunity_met",
      "--at",
      LATER,
      "--source",
      met,
      "--line",
      1,
      "--quote",
      "chance succeeded",
      "--apply",
    )
    again = self.lines("chance returned")
    self.cli(
      "observe",
      "--issue",
      "issue-1",
      "--id",
      "obs-back",
      "--change",
      "fix-1",
      "--outcome",
      "recurrence",
      "--at",
      LATER,
      "--source",
      again,
      "--line",
      1,
      "--quote",
      "chance returned",
      "--apply",
    )
    self.cli(
      "observe",
      "--issue",
      "issue-1",
      "--id",
      "obs-met",
      "--change",
      "fix-1",
      "--outcome",
      "opportunity_met",
      "--at",
      LATER,
      "--source",
      met,
      "--line",
      1,
      "--quote",
      "chance succeeded",
      "--apply",
    )
    listed = self.cli("list", "--issue", "issue-1")
    stats = listed["issues"][0]["observations"]
    self.assertEqual(stats["correct"], 1)
    self.assertEqual(stats["recurrence"], 1)
    self.assertEqual(stats["total"], 2)
    self.assertIsNone(stats["effective"])
    self.assertEqual(stats["evidence"], "observed")
    self.assertNotIn("有效", json.dumps(listed, ensure_ascii=False))

  def test_damaged_line_and_symlink_are_rejected(self):
    self.store.mkdir()
    target = self.store / "records.jsonl"
    target.write_text("{not json}\n", encoding="utf-8")
    path, _body = self.packet()
    error = self.cli("ingest", "--packet", path, "--apply", code=2)
    self.assertIn("damaged", error["error"])
    self.assertEqual(target.read_text(encoding="utf-8"), "{not json}\n")
    target.unlink()
    outside = self.root / "elsewhere.jsonl"
    outside.write_text("", encoding="utf-8")
    target.symlink_to(outside)
    error = self.cli("list", code=2)
    self.assertIn("regular file", error["error"])
    self.assertEqual(outside.read_text(encoding="utf-8"), "")
    linked = self.root / "linked-store"
    linked.symlink_to(self.store, target_is_directory=True)
    error = self.cli("--store", linked, "list", code=2)
    self.assertIn("real directory", error["error"])

  def test_wrapper_without_quote_does_not_pass(self):
    document = self.lines("CANONICAL\nreal line")
    path, body = self.packet()
    body["source_document"] = {"path": str(document), "canonical": "CANONICAL"}
    body["discoveries"][0]["source"] = {
      "path": str(self.lines("quoted elsewhere")),
      "line": 1,
      "quote": "not on this line",
    }
    body["events"] = []
    dump(path, body)
    error = self.cli("ingest", "--packet", path, "--apply", code=2)
    self.assertIn("quote", error["error"])
    self.assertFalse((self.store / "records.jsonl").exists())

  def test_discovery_keeps_raw_records_unchanged(self):
    path, body = self.packet()
    raw = {"quote": "original event text", "extra": ["keep", {"n": 1}]}
    raw_reviews = [{"quote": "reviewer confirmed the event", "kept": True}]
    body["discoveries"][0]["raw"] = raw
    body["discoveries"][0]["raw_reviews"] = raw_reviews
    dump(path, body)
    self.cli("ingest", "--packet", path, "--apply")
    stored = next(row for row in self.rows() if row["type"] == "discovery")
    self.assertEqual(stored["raw"], raw)
    self.assertEqual(stored["raw_reviews"], raw_reviews)

  def test_later_hold_blocks_change(self):
    path, _body = self.packet()
    self.cli("ingest", "--packet", path, "--apply")
    first = self.human("approve this issue", uuid="approve-uuid")
    self.cli(
      "decide",
      "--issue",
      "issue-1",
      "--action",
      "approve",
      "--source",
      first,
      "--line",
      1,
      "--quote",
      "approve this issue",
      "--apply",
    )
    second = self.human("hold this issue", uuid="hold-uuid")
    self.cli(
      "decide",
      "--issue",
      "issue-1",
      "--action",
      "hold",
      "--source",
      second,
      "--line",
      1,
      "--quote",
      "hold this issue",
      "--apply",
    )
    change = self.lines("observable condition source")
    error = self.cli(
      "change",
      "--issue",
      "issue-1",
      "--id",
      "fix-1",
      "--effective-at",
      WHEN,
      "--source",
      change,
      "--line",
      1,
      "--quote",
      "observable condition source",
      "--condition",
      "next handoff keeps the id",
      "--apply",
      code=2,
    )
    self.assertIn("not approved", error["error"])
    self.assertEqual(len([row for row in self.rows() if row["type"] == "change"]), 0)

  def test_default_store_ignores_cwd(self):
    other = self.new("elsewhere")
    other.mkdir()
    fake_root = self.new("fake-repo")
    nested = fake_root / "scripts/local-analysis"
    nested.mkdir(parents=True)
    fake_script = nested / "retro-store.py"
    fake_script.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    probe = self.new("probe-default.py")
    probe.write_text(
      "import importlib.util\n"
      "from pathlib import Path\n"
      f"spec = importlib.util.spec_from_file_location('retro_store', {str(fake_script)!r})\n"
      "module = importlib.util.module_from_spec(spec)\n"
      "spec.loader.exec_module(module)\n"
      "expected = str(Path(module.__file__).resolve().parents[2] / "
      "'reports/local-analysis/retro-accumulation')\n"
      "assert module.DEFAULT_STORE == expected, module.DEFAULT_STORE\n"
      "assert Path(module.DEFAULT_STORE).is_absolute()\n",
      encoding="utf-8",
    )
    result = subprocess.run(
      [sys.executable, "-I", probe], cwd=other, text=True, capture_output=True
    )
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    expected = str(fake_root / "reports/local-analysis/retro-accumulation")
    listed = subprocess.run(
      [sys.executable, "-I", fake_script, "list"], cwd=other, text=True, capture_output=True
    )
    payload = json.loads(listed.stdout or listed.stderr)
    if (Path(expected) / "records.jsonl").exists():
      self.assertEqual(listed.returncode, 0, listed.stderr)
      self.assertIn("issues", payload)
    else:
      self.assertEqual(payload["issues"], [])
    self.assertFalse((other / "reports").exists())

  def test_line_separator_stays_inside_its_line(self):
    path = self.lines("alpha beta")
    packet, body = self.packet()
    body["discoveries"][0]["source"] = {"path": str(path), "line": 1, "quote": "alpha beta"}
    body["events"][0]["anchors"] = [body["discoveries"][0]["source"]]
    body["events"][0]["review_sources"] = [{"path": str(path), "line": 1, "quote": "alpha beta"}]
    dump(packet, body)
    summary = self.cli("ingest", "--packet", packet, "--apply")
    self.assertEqual(summary["appended"], 4)
    human = self.new("human.jsonl")
    human.write_text(
      json.dumps(
        {
          "type": "user",
          "timestamp": WHEN,
          "uuid": "line-uuid",
          "message": {"role": "user", "content": "approve this"},
        }
      )
      + "\n",
      encoding="utf-8",
    )
    self.cli(
      "decide",
      "--issue",
      "issue-1",
      "--action",
      "approve",
      "--source",
      human,
      "--line",
      1,
      "--quote",
      "approve this",
      "--apply",
    )
    self.assertEqual(sum(row["type"] == "decision" for row in self.rows()), 1)

  def test_unknown_issue_decision_is_rejected(self):
    path, _body = self.packet()
    self.cli("ingest", "--packet", path, "--apply")
    human = self.human("approve missing")
    before = len(self.rows())
    error = self.cli(
      "decide",
      "--issue",
      "missing",
      "--action",
      "approve",
      "--source",
      human,
      "--line",
      1,
      "--quote",
      "approve missing",
      "--apply",
      code=2,
    )
    self.assertIn("unknown issue", error["error"])
    self.assertEqual(len(self.rows()), before)

  def test_named_list_keeps_next_condition_and_sources(self):
    path, _body = self.packet()
    self.cli("ingest", "--packet", path, "--apply")
    human = self.human("approve this issue")
    self.cli(
      "decide",
      "--issue",
      "issue-1",
      "--action",
      "approve",
      "--source",
      human,
      "--line",
      1,
      "--quote",
      "approve this issue",
      "--apply",
    )
    evidence = self.lines("progress evidence")
    self.cli(
      "progress",
      "--issue",
      "issue-1",
      "--state",
      "planned",
      "--note",
      "landed",
      "--next",
      "watch the next handoff",
      "--source",
      evidence,
      "--line",
      1,
      "--quote",
      "progress evidence",
      "--apply",
    )
    change = self.lines("observable condition source")
    self.cli(
      "change",
      "--issue",
      "issue-1",
      "--id",
      "fix-1",
      "--effective-at",
      WHEN,
      "--source",
      change,
      "--line",
      1,
      "--quote",
      "observable condition source",
      "--condition",
      "next handoff keeps the id",
      "--apply",
    )
    brief = self.cli("list")
    self.assertNotIn("records", brief["issues"][0])
    named = self.cli("list", "--issue", "issue-1")
    records = named["issues"][0]["records"]
    self.assertEqual(records["progress_entries"][0]["next"], "watch the next handoff")
    self.assertEqual(records["progress_entries"][0]["source"]["quote"], "progress evidence")
    self.assertEqual(records["change_entries"][0]["condition"], "next handoff keeps the id")
    self.assertEqual(records["decisions"][0]["source"]["quote"], "approve this issue")
    self.assertEqual(records["discoveries"][0]["scope"], "session-a")
    self.assertTrue(records["events"])

  def test_non_chance_observations_stay_insufficient(self):
    path, _body = self.packet()
    self.cli("ingest", "--packet", path, "--apply")
    human = self.human("approve this issue")
    self.cli(
      "decide",
      "--issue",
      "issue-1",
      "--action",
      "approve",
      "--source",
      human,
      "--line",
      1,
      "--quote",
      "approve this issue",
      "--apply",
    )
    change = self.lines("observable condition source")
    self.cli(
      "change",
      "--issue",
      "issue-1",
      "--id",
      "fix-1",
      "--effective-at",
      WHEN,
      "--source",
      change,
      "--line",
      1,
      "--quote",
      "observable condition source",
      "--condition",
      "next handoff keeps the id",
      "--apply",
    )
    note = self.lines("not a chance")
    self.cli(
      "observe",
      "--issue",
      "issue-1",
      "--id",
      "obs-na",
      "--change",
      "fix-1",
      "--outcome",
      "not_applicable",
      "--at",
      LATER,
      "--source",
      note,
      "--line",
      1,
      "--quote",
      "not a chance",
      "--apply",
    )
    stats = self.cli("list", "--issue", "issue-1")["issues"][0]["observations"]
    self.assertEqual(stats["evidence"], "insufficient_evidence")
    self.assertEqual(stats["correct"], 0)
    self.assertEqual(stats["recurrence"], 0)
    self.assertIsNone(stats["effective"])

  def test_missing_store_lists_empty(self):
    missing = self.new("missing-store")
    listed = self.cli("--store", missing, "list")
    self.assertEqual(listed["issues"], [])
    self.assertFalse(missing.exists())

  def test_same_event_adds_only_new_points_and_links(self):
    path, body = self.packet()
    self.cli("ingest", "--packet", path, "--apply")
    original = body["events"][0]["anchors"][0]
    first = self.pointer("added anchor and different quote")
    body["events"][0]["anchors"] = [original, first]
    dump(path, body)
    second = self.cli("ingest", "--packet", path, "--apply")
    self.assertEqual(second["appended"], 1)
    body["issues"].append({"id": "issue-2", "title": "Linked later", "related_to": []})
    body["events"][0]["issue_ids"] = ["issue-1", "issue-2"]
    later = self.pointer("third round anchor")
    body["events"][0]["anchors"] = [original, first, later]
    dump(path, body)
    third = self.cli("ingest", "--packet", path, "--apply")
    self.assertEqual(third["appended"], 3)
    self.assertEqual(sum(row["type"] == "event" for row in self.rows()), 1)
    evidence = [row for row in self.rows() if row["type"] == "event_evidence"]
    self.assertEqual(len(evidence), 2)
    self.assertTrue(all("anchors" not in row and "review_sources" not in row for row in evidence))
    replay = self.cli("ingest", "--packet", path, "--apply")
    self.assertEqual(replay["appended"], 0)
    listed = self.cli("list")
    self.assertEqual(listed["issues"][0]["events"], ["event:episode-1"])
    self.assertEqual(listed["issues"][1]["events"], ["event:episode-1"])
    linked = self.cli("list", "--issue", "issue-2")["issues"][0]["records"]["events"][0]
    self.assertEqual(linked["session"], "session-a")
    self.assertEqual(linked["event_at"], WHEN)
    self.assertEqual(linked["anchors"], [original])
    changed = dict(first)
    changed["quote"] = "different quote"
    body["events"][0]["anchors"] = [original, changed, later]
    dump(path, body)
    error = self.cli("ingest", "--packet", path, "--apply", code=2)
    self.assertIn("conflicting source quote", error["error"])
    self.assertEqual(len([row for row in self.rows() if row["type"] == "event_evidence"]), 2)

  def test_progress_replay_tolerates_missing_next(self):
    path, _body = self.packet()
    self.cli("ingest", "--packet", path, "--apply")
    human = self.human("approve this issue")
    self.cli(
      "decide",
      "--issue",
      "issue-1",
      "--action",
      "approve",
      "--source",
      human,
      "--line",
      1,
      "--quote",
      "approve this issue",
      "--apply",
    )
    evidence = self.lines("progress evidence")
    self.cli(
      "progress",
      "--issue",
      "issue-1",
      "--state",
      "planned",
      "--note",
      "landed",
      "--source",
      evidence,
      "--line",
      1,
      "--quote",
      "progress evidence",
      "--apply",
    )
    target = self.store / "records.jsonl"
    rows = [
      json.loads(line) for line in target.read_text(encoding="utf-8").split("\n") if line.strip()
    ]
    self.assertNotIn("next", rows[-1])
    again = self.cli(
      "progress",
      "--issue",
      "issue-1",
      "--state",
      "planned",
      "--note",
      "landed",
      "--next",
      "watch next",
      "--source",
      evidence,
      "--line",
      1,
      "--quote",
      "progress evidence",
      "--apply",
    )
    self.assertEqual(again["appended"], 1)
    self.assertEqual(again["id"], "progress:issue-1:2")
    self.assertEqual(
      self.cli("list", "--issue", "issue-1")["issues"][0]["progress"]["state"], "planned"
    )

  def test_append_repairs_missing_final_newline_as_text(self):
    path, _body = self.packet()
    self.cli("ingest", "--packet", path, "--apply")
    target = self.store / "records.jsonl"
    target.write_bytes(target.read_bytes().rstrip(b"\n"))
    other, body = self.packet()
    body["run"]["id"] = "run-2"
    body["issues"][0]["id"] = "issue-2"
    body["discoveries"][0]["id"] = "disc-2"
    body["discoveries"][0]["issue_id"] = "issue-2"
    body["events"][0]["id"] = "episode-2"
    body["events"][0]["issue_ids"] = ["issue-2"]
    dump(other, body)
    summary = self.cli("ingest", "--packet", other, "--apply")
    self.assertEqual(summary["appended"], 4)
    text = target.read_text(encoding="utf-8")
    self.assertTrue(text.endswith("\n"))
    self.assertEqual(len([line for line in text.split("\n") if line.strip()]), 8)

  def test_bare_cr_keeps_source_line_numbers(self):
    source = self.new("bare-cr.txt")
    source.write_bytes(b"first line\rsecond line\n")
    path, body = self.packet()
    body["discoveries"][0]["source"] = {
      "path": str(source),
      "line": 1,
      "quote": "first line\rsecond line",
    }
    body["discoveries"][0]["review_sources"] = []
    body["events"][0]["anchors"] = [
      {"path": str(source), "line": 1, "quote": "first line\rsecond line"}
    ]
    body["events"][0]["review_sources"] = [{"path": str(source), "line": 1, "quote": "second line"}]
    dump(path, body)
    summary = self.cli("ingest", "--packet", path, "--apply")
    self.assertEqual(summary["appended"], 4)
    body["run"]["id"] = "run-cr"
    body["discoveries"][0]["id"] = "disc-cr"
    body["events"][0]["id"] = "episode-cr"
    body["events"][0]["anchors"] = [{"path": str(source), "line": 2, "quote": "second line"}]
    body["events"][0]["review_sources"] = [{"path": str(source), "line": 2, "quote": "second line"}]
    other = self.new("bare-cr-packet.json")
    dump(other, body)
    error = self.cli("ingest", "--packet", other, "--apply", code=2)
    self.assertIn("quote", error["error"])

  def test_confirmed_event_without_anchors_is_rejected(self):
    path, body = self.packet()
    body["events"][0]["anchors"] = []
    dump(path, body)
    error = self.cli("ingest", "--packet", path, "--apply", code=2)
    self.assertIn("anchors", error["error"])
    self.assertFalse((self.store / "records.jsonl").exists())

  def test_dry_run_summary_is_not_the_whole_store(self):
    path, _body = self.packet()
    summary = self.cli("ingest", "--packet", path)
    self.assertEqual(set(summary), {"ok", "command", "apply", "appended", "noop", "ids"})
    self.assertFalse((self.store / "records.jsonl").exists())


if __name__ == "__main__":
  unittest.main()
