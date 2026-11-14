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

    report1 = self.root / "report1.md"
    report1.write_text("# Empty report\n\n<!-- session-audit-ledger-findings: [] -->\n", encoding="utf-8")
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
        str(report1),
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

  def test_commit_cursor_rejects_missing_report_or_missing_marker(self):
    cursor_path = self.state_dir / "consume-cursor.json"
    cursor_path.write_text(
      json.dumps({"last_finding_id": 3, "last_rule_tag_id": 2, "updated_at": None, "history": []}) + "\n",
      encoding="utf-8",
    )
    cursor_before = cursor_path.read_bytes()
    db_before = self.db_path.read_bytes()
    missing_report = self.root / "missing.md"
    unmarked_report = self.root / "unmarked.md"
    unmarked_report.write_text("# Report without findings marker\n", encoding="utf-8")

    for report_path, expected_error in (
      (missing_report, f"report file not found: {missing_report}"),
      (unmarked_report, f"missing embedded session-audit-ledger-findings marker in {unmarked_report}"),
    ):
      with self.subTest(report_path=report_path):
        result = subprocess.run(
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
            str(report_path),
          ],
          text=True,
          capture_output=True,
          check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        response = json.loads(result.stdout)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], expected_error)
        self.assertEqual(cursor_path.read_bytes(), cursor_before)
        self.assertEqual(self.db_path.read_bytes(), db_before)

  def test_commit_cursor_without_report(self):
    result = subprocess.run(
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
      ],
      text=True,
      capture_output=True,
      check=True,
    )
    response = json.loads(result.stdout)
    self.assertTrue(response["ok"])
    self.assertEqual(response["ingested_findings"], 0)
    self.assertEqual(response["cursor"]["last_finding_id"], 11)
    self.assertEqual(response["cursor"]["last_rule_tag_id"], 5)
    self.assertEqual(json.loads((self.state_dir / "consume-cursor.json").read_text(encoding="utf-8")), response["cursor"])

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

  def test_commit_cursor_ingests_embedded_findings_idempotently_and_aborts_on_bad_ledger(self):
    report_path = self.root / "2026-10-31-session-audit-consume.md"
    ledger_path = self.root / "pending-actions.jsonl"
    embedded = [
      {
        "title": "session-audit-consume：修復 R01 反引號連結攔截",
        "source_key": "session-audit-consume:r01-backtick-link",
        "note": "R01 展示樣例確認真違規",
      }
    ]
    report_path.write_text(
      "# Report\n\nBody text.\n\n"
      f"<!-- session-audit-ledger-findings: {json.dumps(embedded, ensure_ascii=False)} -->\n",
      encoding="utf-8",
    )

    c1 = subprocess.run(
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
        "--date",
        "2026-10-31",
        "--report",
        str(report_path),
        "--ledger",
        str(ledger_path),
      ],
      text=True,
      capture_output=True,
      check=True,
    )
    out1 = json.loads(c1.stdout)
    self.assertTrue(out1["ok"])
    self.assertEqual(out1["ingested_findings"], 1)
    rows1 = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    self.assertEqual(len(rows1), 1)
    self.assertEqual(rows1[0]["source_key"], "session-audit-consume:r01-backtick-link")
    self.assertEqual(rows1[0]["count"], 1)
    self.assertEqual(rows1[0]["status"], "pending")

    # 同日重播：不得重複新增 row，也不得虛增 count
    c2 = subprocess.run(
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
        "--date",
        "2026-10-31",
        "--report",
        str(report_path),
        "--ledger",
        str(ledger_path),
      ],
      text=True,
      capture_output=True,
      check=True,
    )
    self.assertTrue(json.loads(c2.stdout)["ok"])
    rows2 = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    self.assertEqual(len(rows2), 1)
    self.assertEqual(rows2[0]["count"], 1)

    # 若帳本損壞導致匯入失敗，不得推進 cursor
    broken_ledger = self.root / "broken.jsonl"
    broken_ledger.write_text("NOT-JSON\n", encoding="utf-8")
    c_fail = subprocess.run(
      [
        sys.executable,
        str(SCRIPT),
        "commit-cursor",
        "--state",
        str(self.state_dir),
        "--finding-id",
        "99",
        "--rule-tag-id",
        "99",
        "--date",
        "2026-10-31",
        "--report",
        str(report_path),
        "--ledger",
        str(broken_ledger),
      ],
      text=True,
      capture_output=True,
      check=False,
    )
    self.assertNotEqual(c_fail.returncode, 0)
    cursor_now = json.loads((self.state_dir / "consume-cursor.json").read_text(encoding="utf-8"))
    self.assertEqual(cursor_now["last_finding_id"], 11)
    self.assertEqual(cursor_now["last_rule_tag_id"], 5)

  def test_prepare_enforces_utf8_byte_cap_and_keeps_single_oversized_row_intact(self):
    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM findings")
    # 每筆約 950 UTF-8 bytes，100 筆總量約 95KB > 60,000 bytes，但筆數遠小於 --batch-size 420
    cjk_obs = "繁體中文觀察紀錄" * 30
    cjk_chk = "檢查目標腳本與設定" * 18
    cjk_quo = "引述對話原始片段內容" * 16
    for idx in range(1, 101):
      cur.execute(
        "INSERT INTO findings VALUES (?, 'ino1', 1, 'unresolved', 'hook:utf8', ?, ?, ?, ?, ?)",
        (idx, cjk_obs, cjk_chk, cjk_quo, idx * 2, f"issue-utf8-{idx}"),
      )
    conn.commit()
    conn.close()

    out_cap = self.root / "out_cap"
    proc = subprocess.run(
      [
        sys.executable,
        str(SCRIPT),
        "prepare",
        "--state",
        str(self.state_dir),
        "--out-dir",
        str(out_cap),
        "--batch-size",
        "420",
      ],
      text=True,
      capture_output=True,
      check=True,
    )
    manifest = json.loads(proc.stdout)
    self.assertGreaterEqual(manifest["batch_count"], 2)
    self.assertEqual(sum(b["finding_count"] for b in manifest["batches"]), 100)
    for b in manifest["batches"]:
      raw_bytes = Path(b["path"]).read_bytes()
      self.assertEqual(b["bytes"], len(raw_bytes))
      self.assertLessEqual(len(raw_bytes), 60_000)

    # 單筆超過上限時維持 1 筆獨立成批，不丟失也不死迴圈
    out_tiny = self.root / "out_tiny"
    proc_tiny = subprocess.run(
      [
        sys.executable,
        str(SCRIPT),
        "prepare",
        "--state",
        str(self.state_dir),
        "--out-dir",
        str(out_tiny),
        "--batch-size",
        "420",
        "--max-batch-bytes",
        "300",
      ],
      text=True,
      capture_output=True,
      check=True,
    )
    m_tiny = json.loads(proc_tiny.stdout)
    self.assertEqual(m_tiny["batch_count"], 100)
    self.assertTrue(all(b["finding_count"] == 1 for b in m_tiny["batches"]))

  def test_prepare_separates_pretooluse_gate_blocked_rule_tags_from_executed_violations(self):
    transcript_path = self.root / "s-gate.jsonl"
    lines = [
      json.dumps({
        "type": "assistant",
        "message": {
          "role": "assistant",
          "content": [{"type": "tool_use", "id": "call_blocked_1", "name": "Bash", "input": {"command": "find ~/.claude -name foo"}}],
        },
      }, ensure_ascii=False) + "\n",
      json.dumps({
        "type": "user",
        "message": {
          "role": "user",
          "content": [{
            "type": "tool_result",
            "tool_use_id": "call_blocked_1",
            "is_error": True,
            "content": "PreToolUse:Bash hook error: 偵測到對 `~/.claude` 根目錄執行無排除條件的遞迴搜尋。",
          }],
        },
      }, ensure_ascii=False) + "\n",
      json.dumps({
        "type": "assistant",
        "message": {
          "role": "assistant",
          "content": [{"type": "tool_use", "id": "call_exec_2", "name": "Bash", "input": {"command": "grep -r foo src/"}}],
        },
      }, ensure_ascii=False) + "\n",
      json.dumps({
        "type": "user",
        "message": {
          "role": "user",
          "content": [{
            "type": "tool_result",
            "tool_use_id": "call_exec_2",
            "is_error": False,
            "content": "src/a.txt:1:PreToolUse:Bash hook error: literal text in normal stdout",
          }],
        },
      }, ensure_ascii=False) + "\n",
    ]
    raw_blob = b"".join(line.encode("utf-8") for line in lines)
    transcript_path.write_bytes(raw_blob)

    offsets = []
    pos = 0
    for line in lines:
      offsets.append(pos)
      pos += len(line.encode("utf-8"))

    state2 = self.root / "state_with_segments"
    state2.mkdir(parents=True)
    conn = sqlite3.connect(state2 / "state.sqlite")
    cur = conn.cursor()
    cur.executescript(
      """
      CREATE TABLE sources (
        inode TEXT PRIMARY KEY,
        path TEXT NOT NULL,
        classification TEXT NOT NULL,
        included INTEGER NOT NULL,
        status TEXT NOT NULL,
        generation INTEGER NOT NULL,
        mtime REAL NOT NULL
      );
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
      );
      CREATE TABLE segments (
        id INTEGER PRIMARY KEY,
        inode TEXT NOT NULL,
        generation INTEGER NOT NULL,
        doc_start INTEGER NOT NULL,
        doc_end INTEGER NOT NULL,
        line INTEGER NOT NULL,
        block INTEGER NOT NULL,
        byte_offset INTEGER NOT NULL,
        role TEXT NOT NULL,
        record_type TEXT NOT NULL,
        record_uuid TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        tool_use_id TEXT NOT NULL,
        status TEXT NOT NULL,
        limitations TEXT NOT NULL DEFAULT '[]'
      );
      CREATE TABLE rule_tags (
        id INTEGER PRIMARY KEY,
        inode TEXT NOT NULL,
        generation INTEGER NOT NULL,
        rule_id TEXT NOT NULL,
        rule_path TEXT NOT NULL,
        rule_heading TEXT NOT NULL,
        verdict TEXT NOT NULL,
        source_line INTEGER NOT NULL,
        source_ref TEXT NOT NULL,
        quote TEXT NOT NULL,
        conversation_time TEXT NOT NULL,
        commit_sha TEXT NOT NULL
      );
      """
    )
    cur.execute(
      "INSERT INTO sources VALUES ('inoG', ?, 'human-main', 1, 'complete', 1, 1794000000.0)",
      (str(transcript_path),),
    )
    cur.execute(
      "INSERT INTO segments VALUES (1, 'inoG', 1, 0, 100, 1, 0, ?, 'assistant', 'assistant', 'u1', 'Bash', 'call_blocked_1', 'analyzed', '[]')",
      (offsets[0],),
    )
    cur.execute(
      "INSERT INTO segments VALUES (2, 'inoG', 1, 100, 200, 2, 0, ?, 'user', 'user', 'u2', '-', 'call_blocked_1', 'analyzed', '[]')",
      (offsets[1],),
    )
    cur.execute(
      "INSERT INTO segments VALUES (3, 'inoG', 1, 200, 300, 3, 0, ?, 'assistant', 'assistant', 'u3', 'Bash', 'call_exec_2', 'analyzed', '[]')",
      (offsets[2],),
    )
    cur.execute(
      "INSERT INTO segments VALUES (4, 'inoG', 1, 300, 400, 4, 0, ?, 'user', 'user', 'u4', '-', 'call_exec_2', 'analyzed', '[]')",
      (offsets[3],),
    )
    # R-blocked: 2 筆（1 筆來自 tool_use 對應 PreToolUse error tool_result，1 筆直接引述 PreToolUse error）
    cur.execute(
      "INSERT INTO rule_tags VALUES (1, 'inoG', 1, 'aaaa1111aaaa1111', 'rules/common/code-search.md', 'Code search', 'violated', 1, 's#1', 'find ~/.claude -name foo', '2026-11-10T00:00:00Z', 'c1')"
    )
    cur.execute(
      "INSERT INTO rule_tags VALUES (2, 'inoG', 1, 'aaaa1111aaaa1111', 'rules/common/code-search.md', 'Code search', 'violated', 99, 's#99', 'PreToolUse:Bash hook error: 偵測到無排除遞迴搜尋', '2026-11-10T00:01:00Z', 'c1')"
    )
    # R-executed: 1 筆（雖然 stdout 含字面 PreToolUse 字串，但 is_error=false，屬實際執行）
    cur.execute(
      "INSERT INTO rule_tags VALUES (3, 'inoG', 1, 'bbbb2222bbbb2222', 'CLAUDE.md', '工作紀律', 'violated', 3, 's#3', 'grep -r foo src/', '2026-11-10T00:02:00Z', 'c1')"
    )
    conn.commit()
    conn.close()

    out_seg = self.root / "out_seg"
    proc = subprocess.run(
      [sys.executable, str(SCRIPT), "prepare", "--state", str(state2), "--out-dir", str(out_seg)],
      text=True,
      capture_output=True,
      check=True,
    )
    res = json.loads(proc.stdout)
    self.assertEqual(res["rule_violation_rows"], 3)
    self.assertEqual(res["executed_rule_violation_rows"], 1)
    self.assertEqual(res["gate_blocked_rule_violation_rows"], 2)

    rv_md = (out_seg / "rule-violations.md").read_text(encoding="utf-8")
    self.assertIn("- executed_violation_tags: 1", rv_md)
    self.assertIn("- gate_blocked_unexecuted_tags: 2", rv_md)
    # 實際執行違規排在 R01，全數被 gate 攔下的排在 R02
    self.assertLess(rv_md.index("bbbb2222"), rv_md.index("aaaa1111"))
    self.assertIn("all_blocked_by_pretooluse_gate", rv_md)
    self.assertIn("sample_quote [executed]: grep -r foo src/", rv_md)
    self.assertIn("sample_quote [blocked_by_pretooluse_gate]: find ~/.claude -name foo", rv_md)


if __name__ == "__main__":
  unittest.main()
