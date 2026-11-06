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
    self.assertIn("source_sessions", brief["issues"][0])
    summary_list = self.cli("list", "--summary", "--decision", "approve", "--with-changes")
    self.assertEqual(len(summary_list["issues"]), 1)
    self.assertEqual(summary_list["counts"]["approved"], 1)
    self.assertEqual(summary_list["counts"]["with_changes"], 1)
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


  # ---- ticket 04: ingest writes tracking observations -------------------------------------

  def ready(self, changes=("fix-1", "fix-2")):
    """Issue approved with changes effective at WHEN, set up only through the CLI."""
    path, _body = self.packet()
    self.cli("ingest", "--packet", path, "--apply")
    self.cli(
      "decide", "--issue", "issue-1", "--action", "approve", "--source", self.human("approve this issue"),
      "--line", 1, "--quote", "approve this issue", "--apply",
    )
    for name in changes:
      source = self.lines("condition for " + name)
      self.cli(
        "change", "--issue", "issue-1", "--id", name, "--effective-at", WHEN, "--source", source,
        "--line", 1, "--quote", "condition for " + name, "--condition", "next time it is kept", "--apply",
      )

  def event(
    self, key, change, at, loaded="loaded", basis="instructions_attachment", reason=None, line=3, load_time=None
  ):
    # Same shape as session-retro event-evidence output; the values are written by hand per case.
    return {
      "session": "session-a", "change_id": change, "event_key": key, "event_key_kind": "tool_call_id",
      "observation_key": key + "|" + change, "source_line": line, "event_time": at, "loaded": loaded,
      "basis": basis, "load_line": None, "load_time": load_time, "reason": reason,
    }

  def tracked(self, change, outcome, *pairs, issue="issue-1", contexts=(), **extra):
    """Each pair is an action and its event; each context is (event it supports, its own line, quote)."""
    return {
      "issue_id": issue, "change_id": change, "outcome": outcome,
      "evidence": [
        {"session": e["session"], "source_line": e["source_line"], "quote": quote, "role": "action",
         "event_time": e["event_time"]}
        for e, quote in pairs
      ] + [
        {"session": e["session"], "source_line": line, "quote": quote, "role": "context",
         "event_key": e["event_key"], "event_time": e["event_time"]}
        for e, line, quote in contexts
      ],
      "events": [e for e, _ in pairs],
      **extra,
    }

  def observation_packet(self, run_id, observations, dispositions):
    path, body = self.packet(run={"id": run_id, "source": "fixture", "status": "partial", "cutoff": LATER})
    body["issues"], body["discoveries"], body["events"] = [], [], []
    body["observation_proposals"] = observations
    body["tracking_dispositions"] = dispositions
    dump(path, body)
    return path, body

  def kept(self, key):
    return {"observation_key": key, "disposition": "kept"}

  def cases(self):
    """Every result the ticket names, as (observations, dispositions) plus the hand-written expectations."""
    at = "2026-10-20T03:00:00Z"
    a1 = self.event("toolu_A", "fix-1", at)
    a2 = self.event("toolu_A", "fix-2", at)
    b1 = self.event("toolu_B", "fix-1", "2026-10-20T03:10:00Z", line=4)
    b2 = self.event("toolu_B", "fix-1", "2026-10-20T03:10:00Z", line=6)
    c = self.event("toolu_C", "fix-1", "2026-10-20T03:20:00Z")
    d = self.event(
      "toolu_D", "fix-1", BEFORE, loaded="not_loaded", basis="event_before_effective",
      reason="event happened before the change took effect",
    )
    # The packet claims loaded, but the stored effective_at is later than the event: the store wins.
    d2 = self.event("toolu_D2", "fix-1", BEFORE)
    e = self.event(
      "toolu_E", "fix-1", "2026-10-20T03:30:00Z", loaded="not_loaded", basis="last_load_is_old_version",
      reason="the last load before the event does not contain the new text",
    )
    f = self.event(
      "toolu_F", "fix-1", "2026-10-20T03:40:00Z", loaded="unchecked", basis="unchecked",
      reason="no instructions attachment before the event",
    )
    g = self.event("toolu_G", "fix-1", "2026-10-20T03:50:00Z")
    observations = [
      self.tracked("fix-1", "recurrence", (a1, "grep again"), covered="no"),
      self.tracked("fix-2", "recurrence", (a2, "grep again"), covered="yes"),
      self.tracked("fix-1", "recurrence", (b1, "ls first"), (b2, "ls second")),
      self.tracked("fix-1", "opportunity_met", (c, "did it right")),
      self.tracked("fix-1", "recurrence", (d, "old event")),
      self.tracked("fix-1", "recurrence", (d2, "old event two")),
      self.tracked("fix-1", "recurrence", (e, "not loaded")),
      self.tracked("fix-1", "recurrence", (f, "cannot tell")),
      self.tracked("fix-1", "unreviewed", (g, "later judged unreviewed")),
    ]
    dispositions = [
      self.kept("toolu_A|fix-1"),
      self.kept("toolu_A|fix-2"),
      {
        "observation_key": "toolu_B|fix-1", "disposition": "merged", "merged_into": "toolu_B|fix-1",
        "merged_from": [{"unit": "u1", "issue_id": "issue-1"}, {"unit": "u2", "issue_id": "issue-1"}],
      },
      self.kept("toolu_C|fix-1"),
      self.kept("toolu_D|fix-1"),
      self.kept("toolu_D2|fix-1"),
      self.kept("toolu_E|fix-1"),
      self.kept("toolu_F|fix-1"),
      {
        "observation_key": "toolu_G|fix-1", "disposition": "reclassified", "to_outcome": "unreviewed",
        "reason": "the model only skimmed it",
      },
      {"observation_key": "toolu_H|fix-1", "disposition": "rejected", "reason": "the hook never ran here"},
    ]
    expected = {
      "toolu_A|fix-1": ("recurrence", "no"),
      "toolu_A|fix-2": ("recurrence", "yes"),
      "toolu_B|fix-1": ("recurrence", "unknown"),
      "toolu_C|fix-1": ("opportunity_met", None),
      "toolu_D|fix-1": ("invalid", None),
      "toolu_D2|fix-1": ("invalid", None),
      "toolu_E|fix-1": ("not_loaded", None),
      "toolu_F|fix-1": ("unchecked", None),
      "toolu_G|fix-1": ("unreviewed", None),
    }
    return observations, dispositions, expected

  def observation_rows(self):
    return {
      row["observation_id"]: row for row in self.rows() if row["type"] == "observation" and "event_time" in row
    }

  def test_ingest_writes_every_tracking_observation_with_its_result(self):
    self.ready()
    observations, dispositions, expected = self.cases()
    path, _body = self.observation_packet("run-obs", observations, dispositions)
    summary = self.cli("ingest", "--packet", path, "--apply")
    rows = self.observation_rows()
    self.assertEqual(set(rows), set(expected))
    for key, (result, covered) in expected.items():
      self.assertEqual(rows[key]["outcome"], result, key)
      self.assertEqual(rows[key].get("covered"), covered, key)
      self.assertEqual(rows[key]["issue_id"], "issue-1")
    # Event time comes from the event, never from the cutoff or the moment of writing.
    self.assertEqual(rows["toolu_A|fix-1"]["event_time"], "2026-10-20T03:00:00Z")
    self.assertEqual(rows["toolu_A|fix-1"]["evidence"], {"session": "session-a", "source_line": 3, "quote": "grep again"})
    self.assertEqual(rows["toolu_G|fix-1"]["reported_outcome"], "unreviewed")
    self.assertEqual(rows["toolu_G|fix-1"]["cross_disposition"]["reason"], "the model only skimmed it")
    # Invalid and not-loaded rows say why; the stored effective_at, not the packet claim, decides invalid.
    self.assertTrue(rows["toolu_D|fix-1"]["reason"].strip())
    self.assertTrue(rows["toolu_D2|fix-1"]["reason"].strip())
    self.assertEqual(rows["toolu_E|fix-1"]["reason"], "the last load before the event does not contain the new text")
    self.assertEqual(rows["toolu_F|fix-1"]["reason"], "no instructions attachment before the event")
    # One action, two changes: two observations.
    self.assertEqual(rows["toolu_A|fix-1"]["event_key"], rows["toolu_A|fix-2"]["event_key"])
    counts = summary["observations"]
    self.assertEqual({k: counts[k] for k in ("new", "replayed", "supplemented", "corrected")},
                     {"new": 9, "replayed": 0, "supplemented": 1, "corrected": 0})
    self.assertEqual(counts["rejected"], [{"observation_key": "toolu_H|fix-1", "reason": "the hook never ran here"}])
    self.assertFalse(any("toolu_H" in row["id"] for row in self.rows()))

  def test_only_loaded_recurrences_after_the_change_are_counted(self):
    self.ready()
    observations, dispositions, _expected = self.cases()
    path, _body = self.observation_packet("run-obs", observations, dispositions)
    self.cli("ingest", "--packet", path, "--apply")
    stats = self.cli("list", "--issue", "issue-1")["issues"][0]["observations"]
    # Recurrences: A1, A2, B. D, D2 (before the change), E (not loaded), F (unchecked) are not among them.
    self.assertEqual(stats["recurrence"], 3)
    self.assertEqual(stats["correct"], 1)
    self.assertEqual(stats["invalid"], 2)
    self.assertEqual(stats["not_loaded"], 1)
    self.assertEqual(stats["unchecked"], 1)
    self.assertEqual(stats["total"], 9)
    self.assertEqual(stats["recurrence_by_coverage"], {"yes": 1, "no": 1, "unknown": 1})
    self.assertEqual(stats["legacy"], 0)
    self.assertIsNone(stats["effective"])

  def test_replaying_the_same_packet_adds_nothing(self):
    self.ready()
    observations, dispositions, _expected = self.cases()
    path, _body = self.observation_packet("run-obs", observations, dispositions)
    first = self.cli("ingest", "--packet", path, "--apply")
    self.assertEqual(first["observations"]["new"], 9)
    before = (self.store / "records.jsonl").read_bytes()
    again = self.cli("ingest", "--packet", path, "--apply")
    self.assertEqual(again["appended"], 0)
    self.assertEqual(again["ids"], [])
    counts = again["observations"]
    self.assertEqual({k: counts[k] for k in ("new", "replayed", "supplemented", "corrected")},
                     {"new": 0, "replayed": 10, "supplemented": 0, "corrected": 0})
    self.assertEqual((self.store / "records.jsonl").read_bytes(), before)

  def test_new_evidence_for_a_known_observation_is_a_supplement(self):
    self.ready()
    observations, dispositions, _expected = self.cases()
    first, _ = self.observation_packet("run-obs", observations, dispositions)
    self.cli("ingest", "--packet", first, "--apply")
    base = self.observation_rows()["toolu_C|fix-1"]
    again = self.event("toolu_C", "fix-1", "2026-10-20T03:20:00Z", line=9)
    second, _ = self.observation_packet(
      "run-two", [self.tracked("fix-1", "opportunity_met", (again, "another look"))], [self.kept("toolu_C|fix-1")]
    )
    summary = self.cli("ingest", "--packet", second, "--apply")
    counts = summary["observations"]
    self.assertEqual({k: counts[k] for k in ("new", "replayed", "supplemented", "corrected")},
                     {"new": 0, "replayed": 0, "supplemented": 1, "corrected": 0})
    supplement = next(row for row in self.rows() if row["id"] in summary["ids"] and row["type"] == "observation_evidence")
    self.assertEqual(summary["appended"], 2)  # the new run row and the supplement
    self.assertEqual(supplement["type"], "observation_evidence")
    self.assertEqual(supplement["observation_id"], "toolu_C|fix-1")
    self.assertEqual(supplement["evidence"]["quote"], "another look")
    self.assertEqual(self.observation_rows()["toolu_C|fix-1"], base)
    # Positive control for the "replayed" bucket: the same second packet again is a plain replay.
    third = self.cli("ingest", "--packet", second, "--apply")
    self.assertEqual(third["observations"]["supplemented"], 0)
    self.assertEqual(third["observations"]["replayed"], 1)

  def test_reclassification_is_an_appended_correction_with_a_reason(self):
    self.ready()
    observations, dispositions, _expected = self.cases()
    first, _ = self.observation_packet("run-obs", observations, dispositions)
    self.cli("ingest", "--packet", first, "--apply")
    original = self.observation_rows()["toolu_A|fix-1"]
    event = self.event("toolu_A", "fix-1", "2026-10-20T03:00:00Z")
    changed = [self.tracked("fix-1", "unreviewed", (event, "grep again"))]
    before = (self.store / "records.jsonl").read_bytes()
    # Without a reclassification reason the changed result is refused and nothing is written.
    silent, _ = self.observation_packet("run-two", changed, [self.kept("toolu_A|fix-1")])
    error = self.cli("ingest", "--packet", silent, "--apply", code=2)
    self.assertIn("reason", error["error"])
    self.assertEqual((self.store / "records.jsonl").read_bytes(), before)
    reasoned = [{
      "observation_key": "toolu_A|fix-1", "disposition": "reclassified", "to_outcome": "unreviewed",
      "reason": "the quote only shows a listing",
    }]
    path, _ = self.observation_packet("run-two", changed, reasoned)
    summary = self.cli("ingest", "--packet", path, "--apply")
    counts = summary["observations"]
    self.assertEqual({k: counts[k] for k in ("new", "replayed", "supplemented", "corrected")},
                     {"new": 0, "replayed": 0, "supplemented": 0, "corrected": 1})
    correction = next(row for row in self.rows() if row["id"] in summary["ids"] and row["type"] == "observation_correction")
    self.assertEqual(correction["type"], "observation_correction")
    self.assertEqual(correction["supersedes"], original["id"])
    self.assertEqual(correction["observation_id"], "toolu_A|fix-1")
    self.assertEqual(correction["outcome"], "unreviewed")
    self.assertEqual(correction["correction_reason"], "the quote only shows a listing")
    # The superseded observation keeps its content; only the latest state changes.
    self.assertEqual(self.observation_rows()["toolu_A|fix-1"], original)
    stats = self.cli("list", "--issue", "issue-1")["issues"][0]["observations"]
    self.assertEqual(stats["recurrence"], 2)
    self.assertEqual(stats["total"], 9)
    again = self.cli("ingest", "--packet", path, "--apply")
    self.assertEqual(again["appended"], 0)
    # Replaying the older run after the correction does not undo it.
    old = self.cli("ingest", "--packet", first, "--apply")
    self.assertEqual(old["observations"]["corrected"], 0)
    self.assertEqual(old["appended"], 0)

  def test_p3_latest_state_matches_cross_output_and_nothing_vanishes(self):
    self.ready()
    observations, dispositions, expected = self.cases()
    first, _ = self.observation_packet("run-obs", observations, dispositions)
    self.cli("ingest", "--packet", first, "--apply")
    event = self.event("toolu_A", "fix-1", "2026-10-20T03:00:00Z")
    second_dispositions = [
      {"observation_key": "toolu_A|fix-1", "disposition": "reclassified", "to_outcome": "unreviewed",
       "reason": "the quote only shows a listing"},
      self.kept("toolu_C|fix-1"),
    ]
    c_again = self.event("toolu_C", "fix-1", "2026-10-20T03:20:00Z")
    second, _ = self.observation_packet(
      "run-two",
      [self.tracked("fix-1", "unreviewed", (event, "grep again")),
       self.tracked("fix-1", "opportunity_met", (c_again, "did it right"))],
      second_dispositions,
    )
    self.cli("ingest", "--packet", second, "--apply")
    # Expected latest state: first run's results, overridden by the second run's cross output.
    final = dict(expected)
    final["toolu_A|fix-1"] = ("unreviewed", None)
    latest = {
      row["observation_id"]: row
      for row in self.cli("list", "--issue", "issue-1")["issues"][0]["records"]["observation_latest"]
    }
    self.assertEqual(set(latest), set(final))
    for key, (result, covered) in final.items():
      self.assertEqual(latest[key]["outcome"], result, key)
    survivors = {
      d["observation_key"] for d in dispositions + second_dispositions if d["disposition"] != "rejected"
    }
    self.assertEqual(set(latest), survivors)
    rejected = {d["observation_key"] for d in dispositions if d["disposition"] == "rejected"}
    self.assertTrue(rejected)
    self.assertFalse(rejected & set(latest))

  def test_legacy_observations_are_listed_apart_and_never_counted(self):
    self.ready()
    # An old observe row: it has a write-time `at` but no event time.
    legacy = {
      "id": "observation:old-1", "type": "observation", "issue_id": "issue-1", "observation_id": "old-1",
      "change_id": "fix-1", "outcome": "recurrence", "at": LATER, "source": self.pointer("old recurrence"),
    }
    with open(self.store / "records.jsonl", "a", encoding="utf-8") as stream:
      stream.write(json.dumps(legacy) + "\n")
    stats = self.cli("list", "--issue", "issue-1")["issues"][0]["observations"]
    self.assertEqual(stats["legacy"], 1)
    self.assertEqual(stats["recurrence"], 0)
    self.assertEqual(stats["total"], 0)
    self.assertEqual(stats["evidence"], "insufficient_evidence")
    summary = self.cli("list", "--summary")
    self.assertEqual(summary["counts"]["legacy_observations"], 1)
    # Positive control: a manual observe with an event time is counted by the same projection.
    manual = self.lines("manual recurrence")
    self.cli(
      "observe", "--issue", "issue-1", "--id", "manual-1", "--change", "fix-1", "--outcome", "recurrence",
      "--at", LATER, "--source", manual, "--line", 1, "--quote", "manual recurrence", "--apply",
    )
    stats = self.cli("list", "--issue", "issue-1")["issues"][0]["observations"]
    self.assertEqual(stats["recurrence"], 1)
    self.assertEqual(stats["legacy"], 1)
    row = next(row for row in self.rows() if row["id"] == "observation:manual-1")
    self.assertEqual(row["event_time"], LATER)
    self.assertEqual(self.cli("list", "--summary")["counts"]["legacy_observations"], 1)

  def test_malformed_observation_section_rejects_the_whole_batch(self):
    self.ready()
    observations, dispositions, _expected = self.cases()
    good = observations[:1]
    before = (self.store / "records.jsonl").read_bytes()

    def drop(name):
      def mutate(item):
        del item[name]
      return mutate

    def shorten(item):
      item["events"] = []

    def retype(item):
      item["outcome"] = 7

    def blank_quote(item):
      item["evidence"][0]["quote"] = ""

    def wrong_key(item):
      item["events"][0]["observation_key"] = "toolu_other|fix-1"

    def bad_coverage(item):
      item["covered"] = "maybe"

    broken = {
      "no events": drop("events"),
      "events do not match evidence": shorten,
      "outcome type": retype,
      "empty quote": blank_quote,
      "key does not match": wrong_key,
      "coverage value": bad_coverage,
    }
    for label, mutate in broken.items():
      second = json.loads(json.dumps(observations[1]))
      mutate(second)
      path, _ = self.observation_packet("run-bad", good + [second], [self.kept("toolu_A|fix-1")])
      error = self.cli("ingest", "--packet", path, "--apply", code=2)
      self.assertIn("observation_proposals[1]", error["error"], label)
      self.assertEqual((self.store / "records.jsonl").read_bytes(), before, label)
    # Positive control: the same two observations without the corruption go in.
    path, _ = self.observation_packet(
      "run-ok", observations[:2], [self.kept("toolu_A|fix-1"), self.kept("toolu_A|fix-2")]
    )
    self.assertEqual(self.cli("ingest", "--packet", path, "--apply")["observations"]["new"], 2)

  # ---- evidence roles (ticket 10 finding F6) ------------------------------------------------

  def too_large(self):
    """Deidentified 11-04 shape: two blown-up searches, each followed by a stop, a narrower retry and a read."""
    s1 = self.event("toolu_S1", "fix-1", "2026-10-20T03:00:00Z", line=2)
    s2 = self.event("toolu_S2", "fix-1", "2026-10-20T03:10:00Z", line=5)
    item = self.tracked(
      "fix-1", "recurrence", (s1, "first blow-up"), (s2, "second blow-up"),
      contexts=[(s1, 3, "TaskStop"), (s1, 4, "narrower search"), (s2, 6, "read the saved output"), (s2, 7, "TaskStop again")],
    )
    # Interleave the way the report writes it: action, its contexts, next action, its contexts.
    ev = item["evidence"]
    item["evidence"] = [ev[0], ev[2], ev[3], ev[1], ev[4], ev[5]]
    return item, [self.kept("toolu_S1|fix-1"), self.kept("toolu_S2|fix-1")]

  def test_only_action_evidence_makes_observations_and_every_context_becomes_a_supplement(self):
    self.ready()
    item, dispositions = self.too_large()
    path, _ = self.observation_packet("run-big", [item], dispositions)
    summary = self.cli("ingest", "--packet", path, "--apply")
    # Six evidence entries, two actions: two observations; the four contexts are supplements of their own event.
    self.assertEqual(set(self.observation_rows()), {"toolu_S1|fix-1", "toolu_S2|fix-1"})
    supplements = sorted(
      (row["observation_id"], row["evidence"]["source_line"], row["evidence"]["quote"], row["evidence"]["role"])
      for row in self.rows() if row["type"] == "observation_evidence"
    )
    self.assertEqual(supplements, [
      ("toolu_S1|fix-1", 3, "TaskStop", "context"), ("toolu_S1|fix-1", 4, "narrower search", "context"),
      ("toolu_S2|fix-1", 6, "read the saved output", "context"), ("toolu_S2|fix-1", 7, "TaskStop again", "context"),
    ])
    counts = summary["observations"]
    self.assertEqual({k: counts[k] for k in ("new", "replayed", "supplemented", "corrected")},
                     {"new": 2, "replayed": 0, "supplemented": 4, "corrected": 0})
    stats = self.cli("list", "--issue", "issue-1")["issues"][0]["observations"]
    self.assertEqual((stats["recurrence"], stats["total"], stats["legacy"]), (2, 2, 0))
    # A context never replaces the action as the observation's own evidence.
    self.assertEqual(self.observation_rows()["toolu_S1|fix-1"]["evidence"],
                     {"session": "session-a", "source_line": 2, "quote": "first blow-up"})
    before = (self.store / "records.jsonl").read_bytes()
    again = self.cli("ingest", "--packet", path, "--apply")
    self.assertEqual(again["appended"], 0)
    self.assertEqual({k: again["observations"][k] for k in ("new", "replayed", "supplemented")},
                     {"new": 0, "replayed": 6, "supplemented": 0})
    self.assertEqual((self.store / "records.jsonl").read_bytes(), before)

  def test_malformed_roles_reject_the_whole_batch(self):
    self.ready()
    observations, _dispositions, _expected = self.cases()
    good = observations[:1]
    before = (self.store / "records.jsonl").read_bytes()

    def drop_event_key(item):
      del item["evidence"][1]["event_key"]

    def foreign_event_key(item):
      item["evidence"][1]["event_key"] = "toolu_Z"

    def one_entry_without_role(item):
      del item["evidence"][1]["role"]

    def unknown_role(item):
      item["evidence"][1]["role"] = "bystander"

    def only_context(item):
      item["evidence"] = [item["evidence"][1]]
      item["events"] = []

    def one_event_too_many(item):
      item["events"].append(item["events"][0])

    base, dispositions = self.too_large()
    broken = {
      "context without event_key": (drop_event_key, "evidence[1]"),
      "context points at another observation's event": (foreign_event_key, "evidence[1]"),
      "some entries have a role and some do not": (one_entry_without_role, "evidence[1].role"),
      "role value": (unknown_role, "evidence[1].role"),
      "recurrence with no action": (only_context, "at least one action"),
      "events do not match the actions": (one_event_too_many, "action evidence one to one"),
    }
    for label, (mutate, reason) in broken.items():
      second = json.loads(json.dumps(base))
      mutate(second)
      path, _ = self.observation_packet("run-bad", good + [second], [self.kept("toolu_A|fix-1")] + dispositions)
      error = self.cli("ingest", "--packet", path, "--apply", code=2)
      self.assertIn("observation_proposals[1]", error["error"], label)
      self.assertIn(reason, error["error"], label)
      self.assertEqual((self.store / "records.jsonl").read_bytes(), before, label)
    # Positive control: the same two observations without the corruption go in, contexts included.
    path, _ = self.observation_packet("run-ok", good + [base], [self.kept("toolu_A|fix-1")] + dispositions)
    counts = self.cli("ingest", "--packet", path, "--apply")["observations"]
    self.assertEqual((counts["new"], counts["supplemented"]), (3, 4))

  def test_evidence_without_roles_is_written_as_legacy_and_never_counted(self):
    self.ready()
    at = "2026-10-20T03:00:00Z"
    roled = self.tracked("fix-1", "recurrence", (self.event("toolu_N", "fix-1", at), "new format"))
    old_events = self.tracked("fix-1", "recurrence", (self.event("toolu_O", "fix-1", at), "old with events"))
    old_events["evidence"].append({"session": "session-a", "source_line": 8, "quote": "second old line"})
    old_bare = {"issue_id": "issue-1", "change_id": "fix-1", "outcome": "opportunity_met", "covered": "yes",
                "evidence": [{"session": "session-b", "source_line": 4, "quote": "pre-event report"}]}
    for item in (old_events, old_bare):
      for point in item["evidence"]:
        point.pop("role", None)
        point.pop("event_time", None)
    old_events["events"] = old_events["events"] * 2  # an old validator wrote one event per evidence entry
    # The cross destinations of the old report name event keys; none of them may reject the batch.
    dispositions = [self.kept("toolu_N|fix-1"), self.kept("toolu_O|fix-1")]
    path, _ = self.observation_packet("run-legacy", [roled, old_events, old_bare], dispositions)
    summary = self.cli("ingest", "--packet", path, "--apply")
    counts = summary["observations"]
    self.assertEqual({k: counts[k] for k in ("new", "replayed", "supplemented", "corrected")},
                     {"new": 1, "replayed": 0, "supplemented": 0, "corrected": 0})
    self.assertEqual(counts["legacy"], {"new": 2, "replayed": 0, "supplemented": 1})
    legacy = [row for row in self.rows() if row["type"] == "observation" and "event_time" not in row]
    self.assertEqual(len(legacy), 2)
    self.assertTrue(all(row["observation_id"].startswith("legacy:") for row in legacy))
    self.assertEqual(sorted((row["outcome"], row["evidence"]["quote"]) for row in legacy),
                     [("opportunity_met", "pre-event report"), ("recurrence", "old with events")])
    # They are listed apart and neither counted nor judged; the roled twin is counted by the same projection.
    stats = self.cli("list", "--issue", "issue-1")["issues"][0]["observations"]
    self.assertEqual((stats["recurrence"], stats["correct"], stats["total"], stats["legacy"]), (1, 0, 1, 2))
    self.assertEqual(self.cli("list", "--summary")["counts"]["legacy_observations"], 2)
    self.assertEqual(self.observation_rows()["toolu_N|fix-1"]["event_time"], at)
    before = (self.store / "records.jsonl").read_bytes()
    again = self.cli("ingest", "--packet", path, "--apply")
    self.assertEqual(again["appended"], 0)
    self.assertEqual(again["observations"]["legacy"], {"new": 0, "replayed": 3, "supplemented": 0})
    self.assertEqual((self.store / "records.jsonl").read_bytes(), before)

  def test_observation_for_an_unknown_change_or_wrong_issue_is_refused(self):
    self.ready(changes=("fix-1",))
    observations, _dispositions, _expected = self.cases()
    before = (self.store / "records.jsonl").read_bytes()
    path, _ = self.observation_packet("run-x", [observations[1]], [self.kept("toolu_A|fix-2")])
    error = self.cli("ingest", "--packet", path, "--apply", code=2)
    self.assertIn("change", error["error"])
    self.assertEqual((self.store / "records.jsonl").read_bytes(), before)
    path, _ = self.observation_packet("run-y", [observations[0]], [self.kept("toolu_A|fix-1")])
    self.assertEqual(self.cli("ingest", "--packet", path, "--apply")["observations"]["new"], 1)

  def test_every_cross_destination_must_land_somewhere(self):
    self.ready()
    observations, dispositions, _expected = self.cases()
    before = (self.store / "records.jsonl").read_bytes()
    # A kept destination whose observation is missing would vanish silently.
    path, _ = self.observation_packet("run-v", observations[:1], [self.kept("toolu_A|fix-1"), self.kept("toolu_Z|fix-1")])
    self.assertIn("toolu_Z|fix-1", self.cli("ingest", "--packet", path, "--apply", code=2)["error"])
    # A rejected destination without a reason, or one that is still cited, is refused.
    path, _ = self.observation_packet(
      "run-w", observations[:1], [self.kept("toolu_A|fix-1"), {"observation_key": "toolu_Y|fix-1", "disposition": "rejected"}]
    )
    self.assertIn("reason", self.cli("ingest", "--packet", path, "--apply", code=2)["error"])
    path, _ = self.observation_packet(
      "run-u", observations[:1], [{"observation_key": "toolu_A|fix-1", "disposition": "rejected", "reason": "no"}]
    )
    self.assertIn("toolu_A|fix-1", self.cli("ingest", "--packet", path, "--apply", code=2)["error"])
    self.assertEqual((self.store / "records.jsonl").read_bytes(), before)
    # Positive control: the consistent version of the same packet is accepted.
    path, _ = self.observation_packet("run-t", observations[:1], [self.kept("toolu_A|fix-1")])
    self.assertEqual(self.cli("ingest", "--packet", path, "--apply")["observations"]["new"], 1)

  # ---- real-data fixes (ticket 10 findings F4, F5) -----------------------------------------

  def test_unreviewed_observation_without_evidence_is_listed_not_written_and_does_not_reject_the_batch(self):
    self.ready()
    keyed = self.tracked("fix-1", "recurrence", (self.event("toolu_A", "fix-1", "2026-10-20T03:00:00Z"), "grep again"))
    bare = {"issue_id": "issue-1", "change_id": "fix-1", "outcome": "unreviewed"}
    no_change = {"issue_id": "issue-1", "change_id": None, "outcome": "unreviewed", "evidence": [], "events": []}
    path, _ = self.observation_packet("run-unkeyed", [keyed, bare, no_change], [self.kept("toolu_A|fix-1")])
    summary = self.cli("ingest", "--packet", path, "--apply")
    counts = summary["observations"]
    self.assertEqual({k: counts[k] for k in ("new", "replayed", "supplemented", "corrected")},
                     {"new": 1, "replayed": 0, "supplemented": 0, "corrected": 0})
    self.assertEqual([(u["issue_id"], u["change_id"]) for u in counts["unkeyed"]],
                     [("issue-1", "fix-1"), ("issue-1", None)])
    self.assertTrue(all(isinstance(u["reason"], str) and u["reason"].strip() for u in counts["unkeyed"]))
    self.assertEqual(sorted(self.observation_rows()), ["toolu_A|fix-1"])
    # Replaying lists them again and writes nothing.
    again = self.cli("ingest", "--packet", path, "--apply")
    self.assertEqual(again["appended"], 0)
    self.assertEqual(len(again["observations"]["unkeyed"]), 2)

  def test_only_unreviewed_without_evidence_is_exempt_every_other_gap_still_rejects(self):
    self.ready()
    event = self.event("toolu_A", "fix-1", "2026-10-20T03:00:00Z")
    good = self.tracked("fix-1", "recurrence", (event, "grep again"))
    before = (self.store / "records.jsonl").read_bytes()
    broken = {
      "recurrence without evidence": {"issue_id": "issue-1", "change_id": "fix-1", "outcome": "recurrence"},
      "unreviewed with evidence but no events": {**self.tracked("fix-1", "unreviewed", (event, "x")), "events": []},
      "unreviewed with evidence and no change": {**self.tracked("fix-1", "unreviewed", (event, "x")), "change_id": None},
      "unreviewed with a wrong outcome type": {"issue_id": "issue-1", "change_id": "fix-1", "outcome": 7},
      "unreviewed without an issue": {"change_id": "fix-1", "outcome": "unreviewed"},
    }
    for label, item in broken.items():
      path, _ = self.observation_packet("run-bad", [good, item], [self.kept("toolu_A|fix-1")])
      error = self.cli("ingest", "--packet", path, "--apply", code=2)
      self.assertIn("observation_proposals[1]", error["error"], label)
      self.assertEqual((self.store / "records.jsonl").read_bytes(), before, label)
    # Positive control: the exempt shape alone goes in.
    path, _ = self.observation_packet("run-ok", [good, {"issue_id": "issue-1", "change_id": "fix-1", "outcome": "unreviewed"}],
                                      [self.kept("toolu_A|fix-1")])
    self.assertEqual(len(self.cli("ingest", "--packet", path, "--apply")["observations"]["unkeyed"]), 1)

  def test_same_event_with_a_different_quote_on_the_same_line_is_a_supplement_not_a_conflict(self):
    self.ready()
    at = "2026-10-20T03:00:00Z"
    event = self.event("toolu_A", "fix-1", at, line=3)
    first = self.tracked("fix-1", "recurrence", (event, "first quote"))
    second = self.tracked("fix-1", "recurrence", (event, "second quote"))
    path, _ = self.observation_packet("run-quotes", [first, second], [self.kept("toolu_A|fix-1")])
    counts = self.cli("ingest", "--packet", path, "--apply")["observations"]
    self.assertEqual({k: counts[k] for k in ("new", "replayed", "supplemented", "corrected")},
                     {"new": 1, "replayed": 0, "supplemented": 1, "corrected": 0})
    extra = [row for row in self.rows() if row["type"] == "observation_evidence"]
    self.assertEqual([(row["observation_id"], row["evidence"]["source_line"], row["evidence"]["quote"]) for row in extra],
                     [("toolu_A|fix-1", 3, "second quote")])
    self.assertEqual(self.observation_rows()["toolu_A|fix-1"]["evidence"]["quote"], "first quote")
    # Replay adds nothing; a later run citing the same line with a third quote is one more supplement.
    replay = self.cli("ingest", "--packet", path, "--apply")
    self.assertEqual((replay["appended"], replay["observations"]["replayed"], replay["observations"]["supplemented"]), (0, 2, 0))
    third = self.tracked("fix-1", "recurrence", (event, "third quote"))
    later, _ = self.observation_packet("run-quotes-two", [third], [self.kept("toolu_A|fix-1")])
    counts = self.cli("ingest", "--packet", later, "--apply")["observations"]
    self.assertEqual((counts["supplemented"], counts["replayed"], counts["new"]), (1, 0, 0))
    self.assertEqual(len([row for row in self.rows() if row["type"] == "observation_evidence"]), 2)
    again = self.cli("ingest", "--packet", later, "--apply")
    self.assertEqual((again["appended"], again["observations"]["replayed"]), (0, 1))

  def test_a_different_outcome_for_the_same_event_in_one_run_is_still_a_conflict(self):
    self.ready()
    event = self.event("toolu_A", "fix-1", "2026-10-20T03:00:00Z")
    first = self.tracked("fix-1", "recurrence", (event, "grep again"))
    second = self.tracked("fix-1", "opportunity_met", (event, "other quote"))
    path, _ = self.observation_packet("run-clash", [first, second], [self.kept("toolu_A|fix-1")])
    before = (self.store / "records.jsonl").read_bytes()
    error = self.cli("ingest", "--packet", path, "--apply", code=2)
    self.assertIn("conflicting observation in one run", error["error"])
    self.assertEqual((self.store / "records.jsonl").read_bytes(), before)

  def test_dry_run_reports_observation_buckets_without_writing(self):
    self.ready()
    observations, dispositions, _expected = self.cases()
    path, _ = self.observation_packet("run-obs", observations, dispositions)
    before = (self.store / "records.jsonl").read_bytes()
    summary = self.cli("ingest", "--packet", path)
    self.assertFalse(summary["apply"])
    self.assertEqual(summary["observations"]["new"], 9)
    self.assertEqual((self.store / "records.jsonl").read_bytes(), before)

  def test_replaying_a_supplement_only_run_after_a_correction_keeps_the_correction(self):
    self.ready()
    observations, dispositions, _expected = self.cases()
    first, _ = self.observation_packet("run-obs", observations, dispositions)
    self.cli("ingest", "--packet", first, "--apply")
    more = self.event("toolu_C", "fix-1", "2026-10-20T03:20:00Z", line=9)
    supplement, _ = self.observation_packet(
      "run-two", [self.tracked("fix-1", "opportunity_met", (more, "another look"))], [self.kept("toolu_C|fix-1")]
    )
    self.cli("ingest", "--packet", supplement, "--apply")
    c = self.event("toolu_C", "fix-1", "2026-10-20T03:20:00Z")
    reasoned = [{"observation_key": "toolu_C|fix-1", "disposition": "reclassified", "to_outcome": "unreviewed",
                 "reason": "on a second read it was not a chance"}]
    third, _ = self.observation_packet("run-three", [self.tracked("fix-1", "unreviewed", (c, "did it right"))], reasoned)
    self.assertEqual(self.cli("ingest", "--packet", third, "--apply")["observations"]["corrected"], 1)
    before = (self.store / "records.jsonl").read_bytes()
    again = self.cli("ingest", "--packet", supplement, "--apply")
    self.assertEqual(again["appended"], 0)
    self.assertEqual(again["observations"]["corrected"], 0)
    self.assertEqual(again["observations"]["replayed"], 1)
    self.assertEqual((self.store / "records.jsonl").read_bytes(), before)
    latest = {
      row["observation_id"]: row
      for row in self.cli("list", "--issue", "issue-1")["issues"][0]["records"]["observation_latest"]
    }
    self.assertEqual(latest["toolu_C|fix-1"]["outcome"], "unreviewed")

  def test_session_loaded_before_the_change_is_invalid_not_a_missed_load(self):
    self.ready()
    after = "2026-10-20T03:00:00Z"
    stale = "the last load before the event does not contain the new text"
    window = "load time is not within effective time <= load time <= event time"
    cases = {
      # The session's last load predates the change: the change did not exist when it started.
      "toolu_S1": self.event("toolu_S1", "fix-1", after, "not_loaded", "last_load_is_old_version", stale, load_time=BEFORE),
      "toolu_S2": self.event("toolu_S2", "fix-1", after, "not_loaded", "load_time_outside_effective_window", window, load_time=BEFORE),
      # The change was already in force at the last load, yet the old text was still what got loaded.
      "toolu_S3": self.event("toolu_S3", "fix-1", after, "not_loaded", "last_load_is_old_version", stale, load_time="2026-10-20T02:30:00Z"),
      "toolu_S4": self.event("toolu_S4", "fix-1", after, "not_loaded", "never_loaded", "skill was never invoked before the event"),
      # The basis alone does not decide: the same basis with a load at or after the change is a missed load.
      "toolu_S5": self.event("toolu_S5", "fix-1", after, "not_loaded", "load_time_outside_effective_window", window, load_time="2026-10-20T04:00:00Z"),
      # Positive control: a load after the change that has the new text is a real recurrence.
      "toolu_S6": self.event("toolu_S6", "fix-1", after, "loaded", "instructions_attachment", None, load_time="2026-10-20T02:30:00Z"),
    }
    observations = [self.tracked("fix-1", "recurrence", (e, "again")) for e in cases.values()]
    path, _ = self.observation_packet("run-session", observations, [self.kept(e["observation_key"]) for e in cases.values()])
    self.cli("ingest", "--packet", path, "--apply")
    rows = self.observation_rows()
    expected = {
      "toolu_S1|fix-1": "invalid", "toolu_S2|fix-1": "invalid", "toolu_S3|fix-1": "not_loaded",
      "toolu_S4|fix-1": "not_loaded", "toolu_S5|fix-1": "not_loaded", "toolu_S6|fix-1": "recurrence",
    }
    self.assertEqual({key: rows[key]["outcome"] for key in expected}, expected)
    for key in ("toolu_S1|fix-1", "toolu_S2|fix-1"):
      self.assertIn("session 載入的版本早於生效時間", rows[key]["reason"])
    for key in ("toolu_S3|fix-1", "toolu_S4|fix-1", "toolu_S5|fix-1"):
      self.assertNotIn("session 載入的版本早於生效時間", rows[key]["reason"])
    stats = self.cli("list", "--issue", "issue-1")["issues"][0]["observations"]
    self.assertEqual((stats["recurrence"], stats["invalid"], stats["not_loaded"]), (1, 2, 3))

  def test_a_load_time_that_is_not_a_time_rejects_the_batch(self):
    self.ready()
    bad = self.event("toolu_S7", "fix-1", "2026-10-20T03:00:00Z", "not_loaded", "last_load_is_old_version", "old", load_time="yesterday")
    path, _ = self.observation_packet("run-bad-load", [self.tracked("fix-1", "recurrence", (bad, "again"))], [])
    error = self.cli("ingest", "--packet", path, "--apply", code=2)
    self.assertIn("load_time", error["error"])
    self.assertEqual([row for row in self.rows() if row["type"] == "observation"], [])


  # ---- real-data fix F8: tracking-context from the store ----------------------------------

  def put(self, relative, text):
    path = self.root / "home" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    return {"path": str(path), "line": 1, "quote": text}

  def change_at(self, change_id, relative, text, effective=WHEN):
    point = self.put(relative, text)
    self.cli(
      "change", "--issue", "issue-1", "--id", change_id, "--effective-at", effective, "--source", point["path"],
      "--line", 1, "--quote", text, "--condition", "kept next time for " + change_id, "--apply",
    )
    return point

  def contexts(self, *args):
    return {item["id"]: item for item in self.cli("tracking-context", *args)["changes"]}

  def test_tracking_context_names_kind_time_text_and_name_for_every_change(self):
    self.ready(changes=())
    expected = {
      "c-rule": ("rules/common/search.md", "rule", None),
      "c-command": ("commands/self-closeout.md", "command", "self-closeout"),
      "c-skill": ("skills/demo-skill/SKILL.md", "skill", "demo-skill"),
      "c-hook": ("hooks/gate.py", "hook", None),
      "c-script": ("scripts/run.sh", "script", None),
      "c-nested": ("skills/demo-skill/scripts/helper.sh", "script", None),
      "c-skill-note": ("skills/demo-skill/tests.md", None, None),
      "c-docs": ("docs/notes.md", None, None),
      "c-rule-json": ("rules/common/data.json", None, None),
      "c-command-sh": ("commands/tests/check.sh", None, None),
    }
    for change_id, (relative, _kind, _name) in expected.items():
      self.change_at(change_id, relative, "new text of " + change_id)
    found = self.contexts()
    self.assertEqual(set(found), set(expected))
    for change_id, (_relative, kind, name) in expected.items():
      item = found[change_id]
      self.assertEqual(item.get("kind"), kind, change_id)
      self.assertEqual(item.get("kind_source"), None if kind is None else "path", change_id)
      self.assertEqual(item.get("name"), name, change_id)
      self.assertEqual(item["effective_time"], WHEN, change_id)
      self.assertEqual(item["new_text"], "new text of " + change_id, change_id)
      self.assertEqual(item["issue_id"], "issue-1", change_id)

  def test_the_latest_backfill_decides_the_kind_and_says_so(self):
    self.ready(changes=())
    self.change_at("c-docs", "docs/notes.md", "docs text")
    self.change_at("c-two", "rules/common/two.md", "two text")
    self.change_at("c-skillbf", "docs/bf.md", "bf text")
    self.change_at("c-plain", "rules/common/plain.md", "plain text")
    self.backfill("c-docs", "script")
    self.backfill("c-two", "hook")
    self.backfill("c-two", "always_on_rule")
    self.backfill("c-skillbf", "skill", self.put("skills/bf-skill/SKILL.md", "skill section"))
    found = self.contexts()
    self.assertEqual((found["c-docs"]["kind"], found["c-docs"]["kind_source"]), ("script", "backfill"))
    # The store's always_on_rule is session-retro's rule; the latest filing wins over an earlier one and over the path.
    self.assertEqual((found["c-two"]["kind"], found["c-two"]["kind_source"]), ("rule", "backfill"))
    self.assertEqual((found["c-skillbf"]["kind"], found["c-skillbf"]["name"]), ("skill", "bf-skill"))
    # Control: without a backfill the kind comes from the path.
    self.assertEqual((found["c-plain"]["kind"], found["c-plain"]["kind_source"]), ("rule", "path"))

  def test_tracking_context_carries_issue_background_and_filters_by_issue(self):
    self.ready(changes=())
    self.change_at("c-rule", "rules/common/search.md", "rule text")
    everything = self.cli("tracking-context")
    issue = next(item for item in everything["issues"] if item["id"] == "issue-1")
    self.assertEqual((issue["title"], issue["source_sessions"], issue["change_ids"]), ("Lost handoff", ["session-a"], ["c-rule"]))
    self.assertEqual(issue["decision"], "approve")
    self.assertEqual(set(self.contexts("--issue", "issue-1")), {"c-rule"})
    self.cli("tracking-context", "--issue", "no-such-issue", code=2)
    # An empty or missing store gives empty lists, not an error.
    empty = self.cli("--store", self.root / "nowhere", "tracking-context")
    self.assertEqual((empty["issues"], empty["changes"]), ([], []))

  def test_tracking_context_is_read_only(self):
    self.ready(changes=())
    self.change_at("c-rule", "rules/common/search.md", "rule text")
    before = (self.store / "records.jsonl").read_bytes()
    self.cli("tracking-context")
    self.assertEqual((self.store / "records.jsonl").read_bytes(), before)

  def test_a_change_from_tracking_context_is_understood_by_session_retro(self):
    self.ready(changes=())
    new_text = "搜尋大目錄時必須排除 node_modules"
    self.change_at("c-rule", "rules/common/search.md", new_text, effective="2026-10-20T00:30:00Z")
    change = self.contexts()["c-rule"]
    session = self.root / "sess-ctx.jsonl"
    rows = [
      {"type": "attachment", "timestamp": "2026-10-20T00:50:00Z", "attachment": {
        "type": "instructions", "files": [{"path": "/fixture/RULES.md", "type": "User", "content": new_text}]}},
      {"type": "assistant", "timestamp": "2026-10-20T01:05:00Z", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_ctx", "name": "Bash", "input": {"command": "ls"}}]}},
    ]
    session.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    result = subprocess.run(
      [sys.executable, "-I", str(Path(__file__).with_name("session-retro.py")), "event-evidence",
       "--session-file", str(session), "--source-line", "2", "--change", json.dumps(change, ensure_ascii=False)],
      text=True, capture_output=True,
    )
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    judged = json.loads(result.stdout)
    self.assertEqual((judged["event_key"], judged["loaded"], judged["basis"]), ("toolu_ctx", "loaded", "instructions_attachment"))


  # ---- ticket 05: verdicts, backfill, proposal decisions, pending list -------------------

  def judge(self, change, verdict="effective", level=2, at="2026-10-21T00:00:00Z", side="checked",
            apply=True, code=0, evidence=None, over=None):
    point = evidence or self.pointer("evidence for " + change + " " + verdict + " " + at)
    flags = {
      "--change": change, "--verdict": verdict, "--level": level, "--model": "model-x",
      "--route": "relay-main", "--runs": 5, "--old-recurrences": 4, "--new-recurrences": 1,
      "--source": point["path"], "--line": point["line"], "--quote": point["quote"],
      "--by": "main", "--side-effects": side, "--at": at,
    }
    flags.update(over or {})
    args = ["verdict"]
    for name, value in flags.items():
      if value is not None:
        args += [name, value]
    if apply:
      args.append("--apply")
    return self.cli(*args, code=code)

  def backfill(self, change, kind, point=None, apply=True, code=0):
    point = point or self.pointer("section for " + change + " " + kind)
    args = ["backfill", "--change", change, "--kind", kind, "--source", point["path"],
            "--line", point["line"], "--quote", point["quote"]]
    if apply:
      args.append("--apply")
    return self.cli(*args, code=code)

  def propose(self, kind, decision, change=None, text="no to that", at="2026-10-21T06:00:00Z",
              uuid=None, source=None, rule=None, apply=True, code=0):
    uuid = uuid or "human-" + str(self.serial + 1)
    path = source or self.human(text, uuid=uuid, timestamp=at)
    args = ["proposal", "--kind", kind, "--decision", decision, "--source", path, "--line", 1,
            "--quote", text]
    if change is not None:
      args += ["--change", change]
    if rule is not None:
      args += ["--rule-path", rule["path"], "--rule-line", rule["line"], "--rule-quote", rule["quote"]]
    if apply:
      args.append("--apply")
    return self.cli(*args, code=code)

  def change_view(self, change, issue="issue-1"):
    views = self.cli("list", "--issue", issue)["issues"][0]["changes"]
    return next(view for view in views if view["id"] == change)

  def pending(self):
    return self.cli("list", "--summary")["pending_decisions"]

  def pending_ids(self, name):
    return [item["change_id"] for item in self.pending()[name]]

  def test_verdict_is_appended_bound_to_one_change_with_every_field(self):
    self.ready()
    point = self.pointer("replay output shows the miss")
    dry = self.judge("fix-1", "ineffective", level=2, apply=False, evidence=point)
    self.assertEqual((dry["appended"], dry["apply"]), (1, False))
    self.assertEqual([row for row in self.rows() if row["type"] == "verdict"], [])
    done = self.judge("fix-1", "ineffective", level=2, evidence=point)
    self.assertEqual(done["appended"], 1)
    rows = [row for row in self.rows() if row["type"] == "verdict"]
    self.assertEqual(len(rows), 1)
    row = rows[0]
    self.assertEqual(row["id"], done["id"])
    expected = {
      "issue_id": "issue-1", "change_id": "fix-1", "verdict": "ineffective", "level": 2,
      "model": "model-x", "route": "relay-main", "runs": 5, "old_recurrences": 4,
      "new_recurrences": 1, "source": point, "judged_by": "main", "side_effects": "checked",
      "at": "2026-10-21T00:00:00Z",
    }
    for key, value in expected.items():
      self.assertEqual(row[key], value, key)
    # Replaying the same verdict adds nothing.
    again = self.judge("fix-1", "ineffective", level=2, evidence=point)
    self.assertEqual((again["appended"], again["noop"]), (0, 1))
    # Each refusal changes exactly one argument of the accepted call above.
    before = (self.store / "records.jsonl").read_bytes()
    refusals = [
      ("unknown change", {"change": "nope"}, None),
      ("verdict must", {"verdict": "great"}, None),
      ("level must", {"level": 5}, None),
      ("level must", {"level": 0}, None),
      ("runs must", {}, {"--runs": -1}),
      ("side_effects must", {"side": "maybe"}, None),
      ("model must", {}, {"--model": " "}),
      ("judged_by must", {}, {"--by": ""}),
      ("not ISO", {}, {"--at": "yesterday"}),
      ("quote is not on the source line", {}, {"--quote": "not on that line"}),
    ]
    for reason, kwargs, over in refusals:
      call = {"change": "fix-1", "verdict": "ineffective", "level": 2, "evidence": point}
      call.update(kwargs)
      error = self.judge(call.pop("change"), call.pop("verdict"), code=2, over=over, **call)
      self.assertIn(reason, error["error"])
    self.assertEqual((self.store / "records.jsonl").read_bytes(), before)

  def test_latest_verdict_wins_and_unjudged_change_shows_insufficient(self):
    self.ready()
    self.judge("fix-1", "ineffective", at="2026-10-21T00:00:00Z")
    self.assertEqual(self.change_view("fix-1")["verdict"], "ineffective")
    self.judge("fix-1", "effective", level=3, at="2026-10-22T00:00:00Z")
    view = self.change_view("fix-1")
    self.assertEqual((view["verdict"], view["level"], view["effective"]), ("effective", 3, True))
    self.assertEqual(len([row for row in self.rows() if row["type"] == "verdict"]), 2)
    bare = self.change_view("fix-2")
    self.assertEqual((bare["verdict"], bare["verdict_id"], bare["level"], bare["effective"]),
                     ("insufficient", None, None, None))

  def test_effective_follows_the_mapping_and_is_never_merged_per_issue(self):
    self.ready(changes=("c-eff", "c-no", "c-harm", "c-weak", "c-none"))
    self.judge("c-eff", "effective")
    self.judge("c-no", "ineffective")
    self.judge("c-harm", "harmful")
    self.judge("c-weak", "insufficient")
    listed = self.cli("list", "--issue", "issue-1")["issues"][0]
    got = {view["id"]: view["effective"] for view in listed["changes"]}
    self.assertEqual(got, {"c-eff": True, "c-no": False, "c-harm": False, "c-weak": None, "c-none": None})
    self.assertIs(got["c-eff"], True)
    self.assertIs(got["c-no"], False)
    # The issue carries several changes with different values, so it has no single value.
    self.assertIsNone(listed["observations"]["effective"])

  def test_backfill_adds_kind_and_anchor_without_touching_the_change_row(self):
    self.ready()
    before = next(row for row in self.rows() if row["id"] == "change:fix-1")
    rule = self.pointer("rule section text")
    script = self.pointer("script section text")
    first = self.backfill("fix-1", "always_on_rule", rule)
    self.assertEqual(first["appended"], 1)
    self.backfill("fix-1", "script", script)
    after = next(row for row in self.rows() if row["id"] == "change:fix-1")
    self.assertEqual(after, before)
    self.assertEqual(len([row for row in self.rows() if row["id"] == "change:fix-1"]), 1)
    items = self.change_view("fix-1")["items"]
    self.assertEqual(
      items, [{"kind": "always_on_rule", "source": rule}, {"kind": "script", "source": script}]
    )
    self.assertEqual(self.change_view("fix-2")["items"], [])
    # Replay is a no-op; refusals leave the store alone.
    self.assertEqual(self.backfill("fix-1", "script", script)["appended"], 0)
    size = (self.store / "records.jsonl").read_bytes()
    self.backfill("nope", "script", script, code=2)
    self.backfill("fix-1", "poem", script, code=2)
    self.backfill("fix-1", "script", {"path": script["path"], "line": 1, "quote": "not there"}, code=2)
    self.assertEqual((self.store / "records.jsonl").read_bytes(), size)

  def test_proposal_decision_needs_a_real_human_quote_and_keeps_its_fields(self):
    self.ready()
    at = "2026-10-21T06:30:00Z"
    done = self.propose("retract", "rejected", change="fix-1", text="do not retract it", at=at, uuid="h-1")
    row = next(row for row in self.rows() if row["type"] == "proposal_decision")
    self.assertEqual(row["id"], done["id"])
    self.assertEqual(row["proposal"], "retract")
    self.assertEqual(row["decision"], "rejected")
    self.assertEqual(row["target"], {"change_id": "fix-1"})
    self.assertEqual(row["decided_at"], at)
    self.assertEqual(row["event_uuid"], "h-1")
    self.assertEqual(row["source"]["quote"], "do not retract it")
    # A rule anchor is a legal target for make_hook, and the same call shape accepts it.
    rule = self.pointer("search rule section")
    hooked = self.propose("make_hook", "accepted", rule=rule, text="yes make it a hook", uuid="h-2")
    stored = next(row for row in self.rows() if row["id"] == hooked["id"])
    self.assertEqual(stored["target"], {"rule": rule})
    # Tool output and wrapped notifications are not a human decision, as for decide.
    tool = self.new("tool.jsonl")
    tool.write_text(json.dumps({
      "type": "user", "timestamp": WHEN, "uuid": "t-1",
      "message": {"role": "user", "content": [{"type": "tool_result", "content": "no to that"}]},
    }) + "\n", encoding="utf-8")
    error = self.propose("retract", "rejected", change="fix-1", source=tool, code=2)
    self.assertIn("tool_result", error["error"])
    notice = self.human("<task-notification>no to that", uuid="n-1")
    error = self.propose("retract", "rejected", change="fix-1", source=notice, code=2)
    self.assertIn("notification", error["error"])
    before = (self.store / "records.jsonl").read_bytes()
    self.propose("retract", "rejected", change="nope", code=2)
    self.propose("burn", "rejected", change="fix-1", code=2)
    self.propose("retract", "maybe", change="fix-1", code=2)
    self.propose("retract", "rejected", code=2)
    self.propose("retract", "rejected", rule=rule, code=2)
    self.assertEqual((self.store / "records.jsonl").read_bytes(), before)

  def test_rejecting_a_proposal_leaves_the_issue_approval_and_later_work_alone(self):
    self.ready()
    self.propose("retract", "rejected", change="fix-1", text="no retract please")
    listed = self.cli("list", "--issue", "issue-1")["issues"][0]
    self.assertEqual(listed["decision"]["action"], "approve")
    evidence = self.lines("progress after rejection")
    note = self.cli(
      "progress", "--issue", "issue-1", "--state", "in_progress", "--note", "still going",
      "--source", evidence, "--line", 1, "--quote", "progress after rejection", "--apply",
    )
    self.assertEqual(note["appended"], 1)
    source = self.lines("another condition")
    changed = self.cli(
      "change", "--issue", "issue-1", "--id", "fix-3", "--effective-at", LATER, "--source", source,
      "--line", 1, "--quote", "another condition", "--condition", "kept", "--apply",
    )
    self.assertEqual(changed["appended"], 1)
    # Positive control: a real decline on the issue does flip approval and blocks the same calls.
    self.cli(
      "decide", "--issue", "issue-1", "--action", "decline", "--source",
      self.human("decline the issue", uuid="decline-uuid"), "--line", 1, "--quote", "decline the issue", "--apply",
    )
    self.assertEqual(self.cli("list", "--issue", "issue-1")["issues"][0]["decision"]["action"], "decline")
    error = self.cli(
      "progress", "--issue", "issue-1", "--state", "blocked", "--note", "x", "--source", evidence,
      "--line", 1, "--quote", "progress after rejection", "--apply", code=2,
    )
    self.assertIn("not approved", error["error"])

  def test_pending_list_names_ineffective_harmful_and_rejudge(self):
    self.ready(changes=("c-no", "c-harm", "c-eff", "c-again"))
    self.judge("c-no", "ineffective", level=3)
    self.judge("c-harm", "harmful", level=2)
    self.judge("c-eff", "effective", level=2)
    self.judge("c-again", "effective", level=2, at="2026-10-21T00:00:00Z")
    # A loaded recurrence after the verdict, and a counter-example before it.
    for name, change, outcome, at in (
      ("late", "c-again", "recurrence", "2026-10-21T08:00:00Z"),
      ("early", "c-eff", "recurrence", "2026-10-20T08:00:00Z"),
      ("met", "c-harm", "opportunity_met", "2026-10-21T08:00:00Z"),
    ):
      point = self.lines("obs " + name)
      self.cli("observe", "--issue", "issue-1", "--id", name, "--change", change, "--outcome", outcome,
               "--at", at, "--source", point, "--line", 1, "--quote", "obs " + name, "--apply")
    pending = self.pending()
    self.assertEqual(self.pending_ids("ineffective"), ["c-no"])
    self.assertEqual(self.pending_ids("harmful"), ["c-harm"])
    self.assertEqual(self.pending_ids("needs_rejudge"), ["c-again"])
    self.assertTrue(self.change_view("c-again")["needs_rejudge"])
    self.assertFalse(self.change_view("c-eff")["needs_rejudge"])
    self.assertFalse(self.change_view("c-harm")["needs_rejudge"])
    # The verdict value is unchanged by the re-judge flag.
    self.assertEqual(self.change_view("c-again")["effective"], True)
    self.assertEqual(pending["ineffective"][0]["level"], 3)
    self.assertEqual(pending["ineffective"][0]["open_proposals"], ["retract", "modify"])

  def test_summary_lists_effective_changes_with_level_side_effects_and_level_four_count(self):
    self.ready(changes=("c-a", "c-b", "c-c", "c-d", "c-e"))
    self.judge("c-a", "effective", level=3, side="checked")
    self.judge("c-b", "effective", level=1, side="unchecked")
    self.judge("c-c", "insufficient", level=4)
    self.judge("c-d", "ineffective", level=4)
    # Went to layer 4 first, then a later verdict from layer 2: no longer layer-4 only.
    self.judge("c-e", "ineffective", level=4, at="2026-10-21T00:00:00Z")
    self.judge("c-e", "ineffective", level=2, at="2026-10-22T00:00:00Z")
    pending = self.pending()
    effective = {item["change_id"]: item for item in pending["effective"]}
    self.assertEqual(set(effective), {"c-a", "c-b"})
    self.assertEqual((effective["c-a"]["level"], effective["c-a"]["side_effects_unchecked"]), (3, False))
    self.assertEqual((effective["c-b"]["level"], effective["c-b"]["side_effects_unchecked"]), (1, True))
    self.assertEqual(self.pending_ids("side_effects_unchecked"), ["c-b"])
    self.assertEqual(pending["level_four_only"], 2)
    self.assertTrue(self.change_view("c-b")["side_effects_unchecked"])
    self.assertFalse(self.change_view("c-a")["side_effects_unchecked"])
    # The existing summary counts are still there.
    self.assertEqual(self.cli("list", "--summary")["counts"]["with_changes"], 1)

  def test_rejected_retract_and_modify_leave_the_list_until_newer_evidence(self):
    self.ready(changes=("c-both", "c-half", "c-yes", "c-open"))
    for change in ("c-both", "c-half", "c-yes", "c-open"):
      self.judge(change, "harmful", at="2026-10-21T00:00:00Z")
    self.assertEqual(sorted(self.pending_ids("harmful")), ["c-both", "c-half", "c-open", "c-yes"])
    # Rejections after the verdict.
    self.propose("retract", "rejected", change="c-both", text="keep it a", at="2026-10-21T06:00:00Z", uuid="r1")
    self.propose("modify", "rejected", change="c-both", text="keep it b", at="2026-10-21T06:01:00Z", uuid="r2")
    self.propose("retract", "rejected", change="c-half", text="keep it c", at="2026-10-21T06:02:00Z", uuid="r3")
    self.propose("retract", "accepted", change="c-yes", text="retract it", at="2026-10-21T06:03:00Z", uuid="r4")
    harmful = {item["change_id"]: item for item in self.pending()["harmful"]}
    # Positive control: the same list still shows the changes nobody refused.
    self.assertEqual(set(harmful), {"c-half", "c-open"})
    self.assertEqual(harmful["c-half"]["open_proposals"], ["modify"])
    self.assertEqual(harmful["c-open"]["open_proposals"], ["retract", "modify"])
    # New evidence after the rejection brings it back; a verdict that is no longer bad does not.
    self.judge("c-both", "harmful", at="2026-10-22T00:00:00Z")
    self.assertIn("c-both", self.pending_ids("harmful"))
    self.judge("c-half", "effective", at="2026-10-22T00:00:00Z")
    self.assertNotIn("c-half", self.pending_ids("harmful"))

  def test_empty_store_summary_has_an_empty_pending_list(self):
    out = self.cli("list", "--summary")
    self.assertEqual(out["pending_decisions"]["level_four_only"], 0)
    for name in ("ineffective", "harmful", "needs_rejudge", "effective", "side_effects_unchecked"):
      self.assertEqual(out["pending_decisions"][name], [])


if __name__ == "__main__":
  unittest.main()
