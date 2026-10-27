#!/usr/bin/env python3
"""session-audit-consume.py 的水位線（checkpoint）與去重分批單元測試。"""

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("session-audit-consume.py")


class SessionAuditConsumeTest(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.root = Path(self.tmp.name)
    self.state_dir = self.root / "state"
    self.state_dir.mkdir(parents=True)
    self.db_path = self.state_dir / "state.sqlite"
    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()
    cur.execute(
      """
      CREATE TABLE sources (
        inode TEXT PRIMARY KEY,
        path TEXT NOT NULL,
        classification TEXT NOT NULL,
        included INTEGER NOT NULL,
        status TEXT NOT NULL,
        generation INTEGER NOT NULL,
        mtime REAL NOT NULL
      )
      """
    )
    cur.execute(
      """
      CREATE TABLE findings (
        id INTEGER PRIMARY KEY,
        inode TEXT NOT NULL,
        generation INTEGER NOT NULL,
        status TEXT NOT NULL,
        target TEXT NOT NULL,
        observation TEXT NOT NULL,
        check_direction TEXT NOT NULL,
        quote TEXT NOT NULL,
        source_line INTEGER NOT NULL,
        issue_ref TEXT NOT NULL
      )
      """
    )
    cur.execute(
      """
      CREATE TABLE rule_tags (
        id INTEGER PRIMARY KEY,
        inode TEXT NOT NULL,
        generation INTEGER NOT NULL,
        rule_id TEXT NOT NULL,
        rule_path TEXT NOT NULL,
        rule_heading TEXT NOT NULL,
        verdict TEXT NOT NULL,
        quote TEXT NOT NULL,
        conversation_time TEXT NOT NULL
      )
      """
    )
    cur.execute(
      "INSERT INTO sources VALUES ('ino1', '/Users/test/s1.jsonl', 'human-main', 1, 'partial', 1, 1792000000.0)"
    )
    cur.execute(
      "INSERT INTO findings VALUES (10, 'ino1', 1, 'unresolved', 'hook:foo', 'obs 1', 'chk 1', 'q 1', 12, 'issue-A')"
    )
    cur.execute(
      "INSERT INTO findings VALUES (11, 'ino1', 1, 'unresolved', 'hook:foo', 'obs 1 dup', 'chk 1', 'q 1b', 20, 'issue-A')"
    )
    cur.execute(
      "INSERT INTO findings VALUES (12, 'ino1', 1, 'uncertain', 'hook:bar', 'obs 2', 'chk 2', 'q 2', 30, 'issue-B')"
    )
    cur.execute(
      "INSERT INTO rule_tags VALUES (5, 'ino1', 1, 'deadbeef12345678', 'CLAUDE.md', '工作紀律', 'violated', 'q r1', '2026-10-15T00:00:00Z')"
    )
    conn.commit()
    conn.close()

  def tearDown(self):
    self.tmp.cleanup()

  def test_prepare_and_checkpoint_prevent_reconsumption(self):
    out1 = self.root / "out1"
    p1 = subprocess.run(
      [sys.executable, str(SCRIPT), "prepare", "--state", str(self.state_dir), "--out-dir", str(out1), "--batch-size", "2"],
      text=True,
      capture_output=True,
      check=True,
    )
    m1 = json.loads(p1.stdout)
    self.assertTrue(m1["ok"])
    self.assertEqual(m1["raw_finding_rows"], 2)
    self.assertEqual(m1["dedup_finding_count"], 1)
    self.assertEqual(m1["cursor_candidate"]["last_finding_id"], 11)
    self.assertEqual(m1["cursor_candidate"]["last_rule_tag_id"], 5)

    subprocess.run(
      [
        sys.executable,
        str(SCRIPT),
        "commit-cursor",
        "--state",
        str(self.state_dir),
        "--finding-id",
        "11",
        "--rule-tag-id",
        "5",
        "--report",
        "/tmp/report1.md",
      ],
      text=True,
      capture_output=True,
      check=True,
    )

    out2 = self.root / "out2"
    p2 = subprocess.run(
      [sys.executable, str(SCRIPT), "prepare", "--state", str(self.state_dir), "--out-dir", str(out2)],
      text=True,
      capture_output=True,
      check=True,
    )
    m2 = json.loads(p2.stdout)
    self.assertEqual(m2["raw_finding_rows"], 0)
    self.assertEqual(m2["dedup_finding_count"], 0)
    self.assertEqual(m2["rule_violation_rows"], 0)

    conn = sqlite3.connect(self.db_path)
    conn.execute(
      "INSERT INTO findings VALUES (15, 'ino1', 1, 'unresolved', 'skill:baz', 'new obs', 'new chk', 'new q', 50, 'issue-C')"
    )
    conn.commit()
    conn.close()

    out3 = self.root / "out3"
    p3 = subprocess.run(
      [sys.executable, str(SCRIPT), "prepare", "--state", str(self.state_dir), "--out-dir", str(out3)],
      text=True,
      capture_output=True,
      check=True,
    )
    m3 = json.loads(p3.stdout)
    self.assertEqual(m3["dedup_finding_count"], 1)
    self.assertEqual(m3["cursor_before"]["last_finding_id"], 11)
    self.assertEqual(m3["cursor_candidate"]["last_finding_id"], 15)

  def test_rule_grouping_preserves_distinct_unknown_rids_and_full_why_text(self):
    wf_dir = self.state_dir / "friction"
    wf_dir.mkdir(parents=True, exist_ok=True)
    shared_prefix = "A" * 55
    (wf_dir / "workflow-general.md").write_text(
      f'- source_ref=rule:CLAUDE.md@1111aaaa1111aaaa why="{shared_prefix}-same"\n'
      f'- source_ref=rule:CLAUDE.md@2222bbbb2222bbbb why="{shared_prefix}-same"\n'
      f'- source_ref=rule:CLAUDE.md@3333cccc3333cccc why="{shared_prefix}-different-tail"\n',
      encoding="utf-8",
    )
    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM rule_tags")
    long_heading = "Dispatch + Verify 工作紀律 > Dispatch — Main session 不幹活"
    cur.execute(
      "INSERT INTO rule_tags VALUES (21, 'ino1', 1, '3d58135eaaaa1111', 'rules/common/dispatch-and-verify.md', ?, 'violated', 'q u1', '2026-10-16T00:00:00Z')",
      (long_heading,),
    )
    cur.execute(
      "INSERT INTO rule_tags VALUES (22, 'ino1', 1, 'd48a54d7bbbb2222', 'rules/common/dispatch-and-verify.md', ?, 'violated', 'q u2', '2026-10-16T00:00:00Z')",
      (long_heading,),
    )
    cur.execute(
      "INSERT INTO rule_tags VALUES (23, 'ino1', 1, '1111aaaa1111aaaa', 'CLAUDE.md', '工作紀律', 'violated', 'q k1', '2026-10-16T00:00:00Z')"
    )
    cur.execute(
      "INSERT INTO rule_tags VALUES (24, 'ino1', 1, '2222bbbb2222bbbb', 'CLAUDE.md', '工作紀律', 'violated', 'q k2', '2026-10-16T00:00:00Z')"
    )
    cur.execute(
      "INSERT INTO rule_tags VALUES (25, 'ino1', 1, '3333cccc3333cccc', 'CLAUDE.md', '工作紀律', 'violated', 'q k3', '2026-10-16T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    out_grp = self.root / "out_grp"
    proc = subprocess.run(
      [sys.executable, str(SCRIPT), "prepare", "--state", str(self.state_dir), "--out-dir", str(out_grp)],
      text=True,
      capture_output=True,
      check=True,
    )
    res = json.loads(proc.stdout)
    self.assertEqual(res["distinct_merged_rules"], 4)
    rv_md = (out_grp / "rule-violations.md").read_text(encoding="utf-8")
    self.assertIn("3d58135e", rv_md)
    self.assertIn("d48a54d7", rv_md)
    self.assertNotIn("3d58135e,d48a54d7", rv_md)
    self.assertIn("1111aaaa,2222bbbb", rv_md)


if __name__ == "__main__":
  unittest.main()
