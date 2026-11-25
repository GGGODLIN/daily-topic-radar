#!/usr/bin/env python3
"""Unit tests for work-state-audit-extract.py."""

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPT_PATH = Path(__file__).with_name("work-state-audit-extract.py")
spec = importlib.util.spec_from_file_location("work_state_audit_extract", SCRIPT_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def make_db(tmpdir: Path, rows: list[tuple[str, str, dict, str]]) -> Path:
  db_path = tmpdir / "claude-mem.db"
  conn = sqlite3.connect(db_path)
  conn.execute(
    """
    CREATE TABLE work_state_entries (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      project TEXT NOT NULL,
      list_name TEXT NOT NULL,
      fields TEXT NOT NULL,
      created_at TEXT NOT NULL,
      created_at_epoch INTEGER NOT NULL
    )
    """
  )
  for project, list_name, fields, iso_ts in rows:
    dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    ep = int(dt.timestamp() * 1000)
    conn.execute(
      "INSERT INTO work_state_entries (project, list_name, fields, created_at, created_at_epoch) VALUES (?, ?, ?, ?, ?)",
      (project, list_name, json.dumps(fields, ensure_ascii=False), iso_ts, ep),
    )
  conn.commit()
  conn.close()
  return db_path


class WorkStateAuditExtractTests(unittest.TestCase):
  def setUp(self) -> None:
    self.today = date(2026, 11, 25)
    self.now_ms = int(datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)

  def test_closed_list_and_case_insensitive_grouping(self) -> None:
    with tempfile.TemporaryDirectory() as td:
      db = make_db(Path(td), [
        ("Projects", "already-closed", {"progress": "step 1"}, "2026-11-20T00:00:00Z"),
        ("projects", "already-closed", {"status": "done"}, "2026-11-21T00:00:00Z"),
        ("projects", "still-open", {"progress": "working"}, "2026-11-24T00:00:00Z"),
      ])
      report = mod.extract_open_work_states(db, self.today, self.now_ms)
      self.assertEqual(report["total_lists"], 2)
      self.assertEqual(report["open_count"], 1)
      self.assertEqual(report["items"][0]["list_name"], "still-open")

  def test_orphan_tasks_detected_when_list_status_done(self) -> None:
    with tempfile.TemporaryDirectory() as td:
      db = make_db(Path(td), [
        ("msw", "worker-x", {"task": "t1", "status": "doing"}, "2026-11-01T00:00:00Z"),
        ("msw", "worker-x", {"status": "done"}, "2026-11-02T00:00:00Z"),
      ])
      report = mod.extract_open_work_states(db, self.today, self.now_ms)
      self.assertEqual(report["open_count"], 1)
      item = report["items"][0]
      self.assertFalse(item["state_open"])
      self.assertEqual(item["open_task_count"], 1)
      self.assertIn("list_already_done_with_orphan_tasks(1)", item["signals"])

  def test_missing_and_existing_worktree_signal(self) -> None:
    with tempfile.TemporaryDirectory() as td:
      existing = Path(td) / "live-wt"
      existing.mkdir()
      missing = Path(td) / "deleted-wt"
      db = make_db(Path(td), [
        ("projects", "gone", {"worktree": str(missing), "progress": "done"}, "2026-11-20T00:00:00Z"),
        ("projects", "alive", {"worktree": str(existing), "progress": "wip"}, "2026-11-24T00:00:00Z"),
      ])
      report = mod.extract_open_work_states(db, self.today, self.now_ms)
      by_name = {i["list_name"]: i for i in report["items"]}
      self.assertTrue(any(s.startswith("worktree_missing:") for s in by_name["gone"]["signals"]))
      self.assertTrue(any(s.startswith("worktree_exists:") for s in by_name["alive"]["signals"]))

  def test_future_date_guard_iso_and_mmdd_without_false_positive_ratio(self) -> None:
    with tempfile.TemporaryDirectory() as td:
      db = make_db(Path(td), [
        ("projects", "sub-renew", {"task": "Plus 12/10 續訂", "status": "todo"}, "2026-11-11T00:00:00Z"),
        ("projects", "jam", {"task": "submit", "deadline": "2027-01-08", "status": "todo"}, "2026-11-22T00:00:00Z"),
        ("projects", "ratio-only", {"progress": "passed 12/12 tests and 2/8 滿分 on 2026-11-20"}, "2026-11-20T00:00:00Z"),
      ])
      report = mod.extract_open_work_states(db, self.today, self.now_ms)
      by_name = {i["list_name"]: i for i in report["items"]}
      self.assertIn("12/10", by_name["sub-renew"]["future_dates"])
      self.assertIn("2027-01-08", by_name["jam"]["future_dates"])
      self.assertEqual(by_name["ratio-only"]["future_dates"], [])

  def test_overflow_3k_flag_and_empty_db(self) -> None:
    with tempfile.TemporaryDirectory() as td:
      big_text = "x" * 1300
      db = make_db(Path(td), [
        ("projects", "l1", {"progress": big_text}, "2026-11-24T00:00:00Z"),
        ("projects", "l2", {"progress": big_text}, "2026-11-24T00:00:00Z"),
      ])
      report = mod.extract_open_work_states(db, self.today, self.now_ms)
      self.assertTrue(report["projects"]["projects"]["overflow_3k"])

      empty_db = Path(td) / "missing.db"
      empty_report = mod.extract_open_work_states(empty_db, self.today, self.now_ms)
      self.assertEqual(empty_report["open_count"], 0)
      self.assertIn("OPEN_COUNT=0", mod.format_markdown(empty_report))


if __name__ == "__main__":
  unittest.main()
