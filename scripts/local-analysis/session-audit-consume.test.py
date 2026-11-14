#!/usr/bin/env python3
"""session-audit-consume.py 的水位線（checkpoint）、列級消費、跨軌預合併與 P1..P5 屬性／巡檢測試。"""

import importlib.util
import json
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("session-audit-consume.py")


def load_consume_module():
  spec = importlib.util.spec_from_file_location("session_audit_consume_mod", SCRIPT)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def load_dual_lane_module():
  dual_path = Path(__file__).with_name("session-audit-dual-lane.test.py")
  spec = importlib.util.spec_from_file_location("session_audit_dual_lane_mod", dual_path)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def oracle_p1_consume_once(consumed_f_ids_per_round, all_ready_f_ids, unready_f_ids_per_round, consumed_rt_ids_per_round, all_ready_rt_ids, unready_rt_ids_per_round):
  """Independent Oracle P1: every ready finding/rule_tag is consumed at most once per round, never when unready, and eventually exactly once."""
  seen_f = set()
  for rnd_idx, (batch_f, unready_f) in enumerate(zip(consumed_f_ids_per_round, unready_f_ids_per_round, strict=True)):
    overlap_unready = set(batch_f) & set(unready_f)
    if overlap_unready:
      return False, f"round {rnd_idx} consumed unready finding ids: {sorted(overlap_unready)}"
    dup = seen_f & set(batch_f)
    if dup:
      return False, f"round {rnd_idx} re-consumed finding ids: {sorted(dup)}"
    seen_f.update(batch_f)
  missing_f = set(all_ready_f_ids) - seen_f
  if missing_f:
    return False, f"finding ids skipped permanently: {sorted(missing_f)}"

  seen_rt = set()
  for rnd_idx, (batch_rt, unready_rt) in enumerate(zip(consumed_rt_ids_per_round, unready_rt_ids_per_round, strict=True)):
    overlap_unready = set(batch_rt) & set(unready_rt)
    if overlap_unready:
      return False, f"round {rnd_idx} consumed unready rule_tag ids: {sorted(overlap_unready)}"
    dup = seen_rt & set(batch_rt)
    if dup:
      return False, f"round {rnd_idx} re-consumed rule_tag ids: {sorted(dup)}"
    seen_rt.update(batch_rt)
  missing_rt = set(all_ready_rt_ids) - seen_rt
  if missing_rt:
    return False, f"rule_tag ids skipped permanently: {sorted(missing_rt)}"
  return True, "ok"


def oracle_p4_shuffle_and_idempotent(premerge_fn, items, rng):
  """Independent Oracle P4: shuffle invariance + idempotence."""
  base = premerge_fn(items)
  base_sig = (len(base), [it["source_line"] for it in base], [tuple(it.get("raw_ids") or [it["id"]]) for it in base])
  shuffled = list(items)
  rng.shuffle(shuffled)
  shuf_out = premerge_fn(shuffled)
  shuf_sig = (len(shuf_out), [it["source_line"] for it in shuf_out], [tuple(it.get("raw_ids") or [it["id"]]) for it in shuf_out])
  if shuf_sig != base_sig:
    return False, f"shuffle changed output: {base_sig} vs {shuf_sig}"
  idem = premerge_fn(base)
  idem_sig = (len(idem), [it["source_line"] for it in idem], [tuple(it.get("raw_ids") or [it["id"]]) for it in idem])
  if idem_sig != base_sig:
    return False, f"not idempotent: {base_sig} vs {idem_sig}"
  return True, "ok"


def oracle_p5_coverage_and_window(items, merged_out):
  """Independent Oracle P5: every input finding has a rep in same (inode, generation) within <= 15 lines; no false merges."""
  placeholders = {"", "unknown", "none", "n/a", "null", "-", "unspecified"}
  by_id = {it["id"]: it for it in items}
  covered = set()
  for rep in merged_out:
    rids = rep.get("raw_ids") or [rep["id"]]
    raw_rep_t = " ".join(str(rep.get("target") or "").strip().lower().split())
    raw_rep_c = " ".join(str(rep.get("category") or "").strip().lower().split())
    rep_key = "" if raw_rep_t in placeholders else raw_rep_t
    rep_cat = "" if raw_rep_c in placeholders else raw_rep_c
    for rid in rids:
      if rid not in by_id:
        return False, f"unknown raw_id {rid} in merged output"
      orig = by_id[rid]
      if orig["inode"] != rep["inode"] or int(orig["generation"]) != int(rep["generation"]):
        return False, f"cross-(inode,generation) merge: {orig} into {rep}"
      if abs(int(orig["source_line"]) - int(rep["source_line"])) > 15:
        return False, f"merged line distance > 15: {orig['source_line']} vs {rep['source_line']}"
      raw_orig_t = " ".join(str(orig.get("target") or "").strip().lower().split())
      raw_orig_c = " ".join(str(orig.get("category") or "").strip().lower().split())
      orig_key = "" if raw_orig_t in placeholders else raw_orig_t
      orig_cat = "" if raw_orig_c in placeholders else raw_orig_c
      if len(rids) > 1:
        if not rep_key and not rep_cat:
          return False, f"empty or placeholder target merged: {rids}"
        if rep_key and orig_key != rep_key:
          return False, f"different target merged: {orig_key} vs {rep_key}"
        if not rep_key and orig_cat != rep_cat:
          return False, f"different category merged: {orig_cat} vs {rep_cat}"
      covered.add(rid)
  if covered != set(by_id.keys()):
    return False, f"dropped input findings: {sorted(set(by_id.keys()) - covered)}"
  return True, "ok"


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
      "INSERT INTO findings (id, inode, generation, status, target, observation, check_direction, quote, source_line, issue_ref) "
      "VALUES (15, 'ino1', 1, 'unresolved', 'skill:baz', 'new obs', 'new chk', 'new q', 50, 'issue-C')"
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


class RowLevelConsumeAndPremergeTest(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.root = Path(self.tmp.name)
    self.state_dir = self.root / "state"
    self.state_dir.mkdir(parents=True)
    self.db_path = self.state_dir / "state.sqlite"
    conn = sqlite3.connect(self.db_path)
    conn.executescript(
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
        issue_ref TEXT NOT NULL,
        lane TEXT NOT NULL DEFAULT 'primary'
      );
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
      );
      CREATE TABLE shadow_cursors (
        inode TEXT NOT NULL,
        generation INTEGER NOT NULL,
        lane TEXT NOT NULL,
        doc_offset INTEGER NOT NULL DEFAULT 0,
        continuity TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        updated_at REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (inode, generation, lane)
      );
      """
    )
    conn.commit()
    conn.close()

  def tearDown(self):
    self.tmp.cleanup()

  def _run_prepare(self, out_dir: Path, bg_mode_file: Path = None) -> dict:
    cmd = [sys.executable, str(SCRIPT), "prepare", "--state", str(self.state_dir), "--out-dir", str(out_dir)]
    if bg_mode_file is not None:
      cmd.extend(["--bg-mode-file", str(bg_mode_file)])
    proc = subprocess.run(cmd, text=True, capture_output=True, check=True)
    return json.loads(proc.stdout)

  def _run_commit(self, finding_id: int, rule_tag_id: int) -> dict:
    proc = subprocess.run(
      [
        sys.executable,
        str(SCRIPT),
        "commit-cursor",
        "--state",
        str(self.state_dir),
        "--finding-id",
        str(finding_id),
        "--rule-tag-id",
        str(rule_tag_id),
      ],
      text=True,
      capture_output=True,
      check=True,
    )
    return json.loads(proc.stdout)

  def test_p1_non_skipping_row_level_consumption_and_legacy_migration(self):
    # Legacy cursor has last_finding_id=10, last_rule_tag_id=5 without consumed_migrated flag.
    (self.state_dir / "consume-cursor.json").write_text(
      json.dumps({"last_finding_id": 10, "last_rule_tag_id": 5, "updated_at": None, "history": []}),
      encoding="utf-8",
    )
    conn = sqlite3.connect(self.db_path)
    # inoA: primary complete, shadow pending -> must NOT be backfilled or consumed yet
    conn.execute("INSERT INTO sources VALUES ('inoA', '/Users/test/a.jsonl', 'human-main', 1, 'complete', 1, 1793000000.0)")
    conn.execute("INSERT INTO shadow_cursors VALUES ('inoA', 1, 'bg-audit-shadow', 0, '', 'pending', 0)")
    conn.execute("INSERT INTO findings VALUES (4, 'inoA', 1, 'unresolved', 'hook:a', 'obs A4', 'chk', 'q4', 10, 'ref-a4', 'primary')")
    conn.execute("INSERT INTO rule_tags VALUES (2, 'inoA', 1, 'aaaa1111aaaa1111', 'CLAUDE.md', 'H', 'violated', 'q2', '2026-11-01T00:00:00Z')")
    # inoB: both lanes complete, id <= legacy cursor -> MUST be backfilled during migration
    conn.execute("INSERT INTO sources VALUES ('inoB', '/Users/test/b.jsonl', 'human-main', 1, 'complete', 1, 1793000000.0)")
    conn.execute("INSERT INTO shadow_cursors VALUES ('inoB', 1, 'bg-audit-shadow', 100, '', 'complete', 0)")
    conn.execute("INSERT INTO findings VALUES (8, 'inoB', 1, 'unresolved', 'hook:b', 'obs B8', 'chk', 'q8', 10, 'ref-b8', 'primary')")
    conn.execute("INSERT INTO rule_tags VALUES (4, 'inoB', 1, 'bbbb2222bbbb2222', 'CLAUDE.md', 'H', 'violated', 'q4', '2026-11-01T00:00:00Z')")
    # inoC: post-cutoff complete, no shadow_cursors row -> ready when eco, blocked when quality
    conn.execute("INSERT INTO sources VALUES ('inoC', '/Users/test/c.jsonl', 'human-main', 1, 'complete', 1, 1794750000.0)")
    conn.execute("INSERT INTO findings VALUES (14, 'inoC', 1, 'unresolved', 'hook:c', 'obs C14', 'chk', 'q14', 10, 'ref-c14', 'primary')")
    conn.execute("INSERT INTO rule_tags VALUES (8, 'inoC', 1, 'cccc3333cccc3333', 'CLAUDE.md', 'H', 'violated', 'q8', '2026-11-15T01:00:00Z')")
    conn.commit()
    conn.close()

    # With quality bg-mode-file, inoC (no shadow_cursors row yet) is treated as pending; only migration backfill runs
    quality_mode = self.root / "bg-quality.json"
    quality_mode.write_text(json.dumps({"mode": "quality"}), encoding="utf-8")
    m_qual = self._run_prepare(self.root / "out_qual", bg_mode_file=quality_mode)
    self.assertEqual(m_qual["dedup_finding_count"], 0)
    self.assertEqual(m_qual["rule_violation_rows"], 0)

    conn = sqlite3.connect(self.db_path)
    rows_f = dict(conn.execute("SELECT id, consumed_at FROM findings").fetchall())
    rows_rt = dict(conn.execute("SELECT id, consumed_at FROM rule_tags").fetchall())
    conn.close()
    self.assertIsNone(rows_f[4])
    self.assertIsNotNone(rows_f[8])
    self.assertIsNone(rows_f[14])
    self.assertIsNone(rows_rt[2])
    self.assertIsNotNone(rows_rt[4])
    self.assertIsNone(rows_rt[8])

    # With eco bg-mode-file, inoC (no shadow_cursors row) IS ready, while inoA (shadow pending) is STILL blocked
    eco_mode = self.root / "bg-eco.json"
    eco_mode.write_text(json.dumps({"mode": "eco"}), encoding="utf-8")
    m_eco = self._run_prepare(self.root / "out_eco", bg_mode_file=eco_mode)
    self.assertEqual(m_eco["dedup_finding_count"], 1)
    self.assertEqual(m_eco["batches"][0]["first_finding_id"], 14)
    self.assertEqual(m_eco["rule_violation_rows"], 1)
    self._run_commit(m_eco["cursor_candidate"]["last_finding_id"], m_eco["cursor_candidate"]["last_rule_tag_id"])

    # Now add inoX (pending shadow, smaller ID=20) and inoY (complete both lanes, larger ID=30)
    conn = sqlite3.connect(self.db_path)
    conn.execute("INSERT INTO sources VALUES ('inoX', '/Users/test/x.jsonl', 'human-main', 1, 'complete', 1, 1793000100.0)")
    conn.execute("INSERT INTO shadow_cursors VALUES ('inoX', 1, 'bg-audit-shadow', 0, '', 'pending', 0)")
    conn.execute("INSERT INTO findings (id, inode, generation, status, target, observation, check_direction, quote, source_line, issue_ref, lane) VALUES (20, 'inoX', 1, 'unresolved', 'hook:x', 'obs X20', 'chk', 'qx', 10, 'ref-x20', 'primary')")
    conn.execute("INSERT INTO rule_tags (id, inode, generation, rule_id, rule_path, rule_heading, verdict, quote, conversation_time) VALUES (20, 'inoX', 1, 'dddd4444dddd4444', 'CLAUDE.md', 'H', 'violated', 'q20', '2026-11-02T00:00:00Z')")

    conn.execute("INSERT INTO sources VALUES ('inoY', '/Users/test/y.jsonl', 'human-main', 1, 'complete', 1, 1793000100.0)")
    conn.execute("INSERT INTO shadow_cursors VALUES ('inoY', 1, 'bg-audit-shadow', 50, '', 'complete', 0)")
    conn.execute("INSERT INTO findings (id, inode, generation, status, target, observation, check_direction, quote, source_line, issue_ref, lane) VALUES (30, 'inoY', 1, 'unresolved', 'hook:y', 'obs Y30', 'chk', 'qy', 10, 'ref-y30', 'primary')")
    conn.execute("INSERT INTO rule_tags (id, inode, generation, rule_id, rule_path, rule_heading, verdict, quote, conversation_time) VALUES (30, 'inoY', 1, 'eeee5555eeee5555', 'CLAUDE.md', 'H', 'violated', 'q30', '2026-11-02T00:00:00Z')")
    conn.commit()
    conn.close()

    m_y = self._run_prepare(self.root / "out_y", bg_mode_file=quality_mode)
    self.assertEqual(m_y["dedup_finding_count"], 1)
    self.assertEqual(m_y["batches"][0]["first_finding_id"], 30)
    self.assertEqual(m_y["rule_violation_rows"], 1)
    self._run_commit(30, 30)

    # Verify inoX (id=20) and inoA (id=4) were NOT stamped despite 4 < 20 < 30!
    conn = sqlite3.connect(self.db_path)
    rows_f2 = dict(conn.execute("SELECT id, consumed_at FROM findings").fetchall())
    rows_rt2 = dict(conn.execute("SELECT id, consumed_at FROM rule_tags").fetchall())
    self.assertIsNone(rows_f2[4])
    self.assertIsNone(rows_f2[20])
    self.assertIsNotNone(rows_f2[30])
    self.assertIsNone(rows_rt2[2])
    self.assertIsNone(rows_rt2[20])
    self.assertIsNotNone(rows_rt2[30])

    # Now finish shadow for inoA and inoX (inoX adds shadow finding id=35)
    conn.execute("UPDATE shadow_cursors SET status = 'complete', doc_offset = 50 WHERE inode IN ('inoA', 'inoX')")
    conn.execute("INSERT INTO findings (id, inode, generation, status, target, observation, check_direction, quote, source_line, issue_ref, lane) VALUES (35, 'inoX', 1, 'unresolved', 'hook:x-shadow', 'obs X35', 'chk', 'qx2', 40, 'ref-x35', 'bg-audit-shadow')")
    conn.commit()
    conn.close()

    m_ax = self._run_prepare(self.root / "out_ax", bg_mode_file=quality_mode)
    self.assertEqual(m_ax["dedup_finding_count"], 3)
    b_txt = Path(m_ax["batches"][0]["path"]).read_text(encoding="utf-8")
    self.assertIn("## F4 ", b_txt)
    self.assertIn("## F20 ", b_txt)
    self.assertIn("## F35 ", b_txt)
    self.assertNotIn("## F30 ", b_txt)
    self.assertEqual(m_ax["rule_violation_rows"], 2)
    self._run_commit(m_ax["cursor_candidate"]["last_finding_id"], m_ax["cursor_candidate"]["last_rule_tag_id"])

    # Next prepare must see 0 rows
    m_empty = self._run_prepare(self.root / "out_empty", bg_mode_file=quality_mode)
    self.assertEqual(m_empty["dedup_finding_count"], 0)
    self.assertEqual(m_empty["rule_violation_rows"], 0)

  def test_p4_p5_cross_lane_premerge_concrete_and_edge_cases(self):
    mod = load_consume_module()
    premerge = mod.premerge_cross_lane_findings

    # 1. Empty & single
    self.assertEqual(premerge([]), [])
    single = [{"id": 1, "inode": "i1", "generation": 1, "lane": "primary", "target": "hook:foo", "source_line": 10}]
    res_single = premerge(single)
    self.assertEqual(len(res_single), 1)
    self.assertEqual(res_single[0]["raw_ids"], [1])

    # 2. Boundary 10, 22, 36 + exact 15 vs 16
    items_10_22_36 = [
      {"id": 1, "inode": "i1", "generation": 1, "lane": "primary", "target": "  Hook:Foo  ", "source_line": 10},
      {"id": 2, "inode": "i1", "generation": 1, "lane": "bg-audit-shadow", "target": "hook:foo", "source_line": 22},
      {"id": 3, "inode": "i1", "generation": 1, "lane": "primary", "target": "hook:foo", "source_line": 36},
    ]
    res_10_22_36 = premerge(items_10_22_36)
    self.assertEqual(len(res_10_22_36), 2)
    self.assertEqual([it["source_line"] for it in res_10_22_36], [10, 36])
    self.assertEqual(res_10_22_36[0]["raw_ids"], [1, 2])
    self.assertEqual(res_10_22_36[1]["raw_ids"], [3])

    # Exact 15 merges, 16 does not
    self.assertEqual(len(premerge([
      {"id": 1, "inode": "i1", "generation": 1, "lane": "primary", "target": "hook:a", "source_line": 10},
      {"id": 2, "inode": "i1", "generation": 1, "lane": "bg-audit-shadow", "target": "hook:a", "source_line": 25},
    ])), 1)
    self.assertEqual(len(premerge([
      {"id": 1, "inode": "i1", "generation": 1, "lane": "primary", "target": "hook:a", "source_line": 10},
      {"id": 2, "inode": "i1", "generation": 1, "lane": "bg-audit-shadow", "target": "hook:a", "source_line": 26},
    ])), 2)

    # 3. Different target, empty/unknown placeholder target, different inode/generation, same-lane-only never falsely merge
    non_merge = [
      {"id": 1, "inode": "i1", "generation": 1, "lane": "primary", "target": "hook:foo", "source_line": 10},
      {"id": 2, "inode": "i1", "generation": 1, "lane": "bg-audit-shadow", "target": "hook:bar", "source_line": 10},
      {"id": 3, "inode": "i1", "generation": 1, "lane": "primary", "target": "", "source_line": 10},
      {"id": 4, "inode": "i1", "generation": 1, "lane": "bg-audit-shadow", "target": "   ", "source_line": 10},
      {"id": 5, "inode": "i2", "generation": 1, "lane": "bg-audit-shadow", "target": "hook:foo", "source_line": 10},
      {"id": 6, "inode": "i1", "generation": 2, "lane": "bg-audit-shadow", "target": "hook:foo", "source_line": 10},
      {"id": 7, "inode": "i1", "generation": 1, "lane": "primary", "target": "hook:solo", "source_line": 10},
      {"id": 8, "inode": "i1", "generation": 1, "lane": "primary", "target": "hook:solo", "source_line": 14},
      {"id": 9, "inode": "i1", "generation": 1, "lane": "primary", "target": "unknown", "source_line": 10},
      {"id": 10, "inode": "i1", "generation": 1, "lane": "bg-audit-shadow", "target": "Unknown", "source_line": 12},
    ]
    self.assertEqual(len(premerge(non_merge)), 10)

    # 4. End-to-end prepare + partial commit-cursor stamps whole premerged cluster by rep_id
    conn = sqlite3.connect(self.db_path)
    conn.execute("INSERT INTO sources VALUES ('inoM', '/Users/test/m.jsonl', 'human-main', 1, 'complete', 1, 1794750000.0)")
    conn.execute("INSERT INTO shadow_cursors VALUES ('inoM', 1, 'bg-audit-shadow', 100, '', 'complete', 0)")
    conn.execute("INSERT INTO findings VALUES (101, 'inoM', 1, 'unresolved', 'Hook:Dup', 'obs p', 'chk', 'qp', 10, 'ref-p', 'primary')")
    conn.execute("INSERT INTO findings VALUES (250, 'inoM', 1, 'unresolved', 'hook:dup', 'obs s', 'chk', 'qs', 22, 'ref-s', 'bg-audit-shadow')")
    conn.execute("INSERT INTO findings VALUES (150, 'inoM', 1, 'unresolved', 'hook:later', 'obs later', 'chk', 'ql', 60, 'ref-l', 'primary')")
    conn.commit()
    conn.close()

    m = self._run_prepare(self.root / "out_merge")
    self.assertEqual(m["raw_finding_rows"], 3)
    self.assertEqual(m["dedup_finding_count"], 2)
    # Truncated commit up to rep_id=101 must stamp BOTH 101 and 250, while leaving rep_id=150 unstamped!
    self._run_commit(101, 0)

    conn = sqlite3.connect(self.db_path)
    stamped = dict(conn.execute("SELECT id, consumed_at FROM findings WHERE id IN (101, 150, 250)").fetchall())
    conn.close()
    self.assertIsNotNone(stamped[101])
    self.assertIsNotNone(stamped[250])
    self.assertIsNone(stamped[150])

  def test_p4_p5_conditional_pbt_premerge_invariants(self):
    mod = load_consume_module()
    premerge = mod.premerge_cross_lane_findings
    rng = random.Random(20261115)
    targets = ["hook:alpha", "  Hook:Alpha  ", "hook:beta", "skill:gamma", "", "   ", "unknown", "Unknown", "N/A"]
    lanes = ["primary", "bg-audit-shadow"]
    inodes = ["ino1", "ino2"]

    for trial in range(50):
      count = rng.randint(0, 25)
      items = []
      for fid in range(1, count + 1):
        items.append({
          "id": fid,
          "inode": rng.choice(inodes),
          "generation": rng.choice([1, 2]),
          "lane": rng.choice(lanes),
          "target": rng.choice(targets),
          "source_line": rng.randint(1, 90),
          "observation": f"obs-{fid}",
          "check_direction": f"chk-{fid}",
          "quote": f"q-{fid}",
          "source": "~/s.jsonl:1",
          "cls": "human-main",
        })
      ok4, msg4 = oracle_p4_shuffle_and_idempotent(premerge, items, rng)
      self.assertTrue(ok4, f"trial={trial}: {msg4}")
      merged = premerge(items)
      ok5, msg5 = oracle_p5_coverage_and_window(items, merged)
      self.assertTrue(ok5, f"trial={trial}: {msg5}")

  def test_oracle_calibration_p1_p4_p5(self):
    # P1 catches skipped smaller ID and duplicate consumption
    ok, _ = oracle_p1_consume_once([[30]], [20, 30], [[]], [[]], [], [[]])
    self.assertFalse(ok)
    ok, _ = oracle_p1_consume_once([[20], [20]], [20], [[], []], [[]], [], [[]])
    self.assertFalse(ok)
    ok, _ = oracle_p1_consume_once([[20]], [20], [[20]], [[]], [], [[]])
    self.assertFalse(ok)

    # P4 catches shuffle-dependent or non-idempotent premerge
    def bad_order_premerge(items):
      return list(items)

    ok4, _ = oracle_p4_shuffle_and_idempotent(
      bad_order_premerge,
      [{"id": 1, "source_line": 10}, {"id": 2, "source_line": 20}],
      random.Random(1),
    )
    self.assertFalse(ok4)

    # P5 catches >15 line merge and empty target merge
    items_far = [
      {"id": 1, "inode": "i1", "generation": 1, "lane": "primary", "target": "hook:a", "source_line": 10},
      {"id": 2, "inode": "i1", "generation": 1, "lane": "bg-audit-shadow", "target": "hook:a", "source_line": 30},
    ]
    bad_far_out = [{**items_far[0], "raw_ids": [1, 2]}]
    ok5_far, _ = oracle_p5_coverage_and_window(items_far, bad_far_out)
    self.assertFalse(ok5_far)

    items_empty = [
      {"id": 1, "inode": "i1", "generation": 1, "lane": "primary", "target": "", "source_line": 10},
      {"id": 2, "inode": "i1", "generation": 1, "lane": "bg-audit-shadow", "target": "", "source_line": 10},
    ]
    bad_empty_out = [{**items_empty[0], "raw_ids": [1, 2]}]
    ok5_empty, _ = oracle_p5_coverage_and_window(items_empty, bad_empty_out)
    self.assertFalse(ok5_empty)


_dual_mod = load_dual_lane_module()


class JointP1ToP5PatrolTest(_dual_mod.DualLaneBaseTests):
  """Bounded interleaving patrol across session-audit.py (run) and session-audit-consume.py (prepare/commit-cursor)."""

  def _consume_round(self):
    out_dir = self.root / f"consume_out_{getattr(self, '_consume_seq', 0)}"
    self._consume_seq = getattr(self, "_consume_seq", 0) + 1
    prep = subprocess.run(
      [
        sys.executable,
        str(SCRIPT),
        "prepare",
        "--state",
        str(self.state),
        "--out-dir",
        str(out_dir),
        "--bg-mode-file",
        str(self.bg_mode_file),
      ],
      text=True,
      capture_output=True,
      check=True,
    )
    manifest = json.loads(prep.stdout)
    pending_file = self.state / "pending-consume.json"
    pending_data = json.loads(pending_file.read_text(encoding="utf-8")) if pending_file.is_file() else {"finding_groups": [], "rule_tag_ids": []}
    if manifest["raw_finding_rows"] > 0 or manifest["rule_violation_rows"] > 0:
      subprocess.run(
        [
          sys.executable,
          str(SCRIPT),
          "commit-cursor",
          "--state",
          str(self.state),
          "--finding-id",
          str(manifest["cursor_candidate"]["last_finding_id"]),
          "--rule-tag-id",
          str(manifest["cursor_candidate"]["last_rule_tag_id"]),
        ],
        text=True,
        capture_output=True,
        check=True,
      )
    finding_ids = sorted({int(rid) for g in pending_data.get("finding_groups", []) for rid in g["raw_ids"]})
    return manifest, finding_ids, pending_data["rule_tag_ids"]

  def _unready_ids(self):
    db_path = self.state / "state.sqlite"
    if not db_path.is_file():
      return set(), set()
    with sqlite3.connect(db_path) as conn:
      conn.row_factory = sqlite3.Row
      bg_mode = json.loads(self.bg_mode_file.read_text())["mode"]
      shadow_cutoff = 1794672000.0  # 2026-11-15T00:00:00+08:00
      has_shadow = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='shadow_cursors'").fetchone() is not None
      unready_sources = set()
      for s in conn.execute("SELECT inode, generation, status, included, mtime FROM sources").fetchall():
        ready = bool(s["included"]) and s["status"] in ("complete", "partial")
        if ready and has_shadow:
          sc_rows = conn.execute(
            "SELECT status FROM shadow_cursors WHERE inode = ? AND generation = ?",
            (s["inode"], s["generation"]),
          ).fetchall()
          sc_statuses = [r["status"] for r in sc_rows]
          if "pending" in sc_statuses:
            ready = False
          elif not sc_statuses and bg_mode == "quality" and float(s["mtime"] or 0.0) >= shadow_cutoff:
            ready = False
        if not ready:
          unready_sources.add((s["inode"], s["generation"]))
      uf = {
        r["id"]
        for r in conn.execute("SELECT id, inode, generation FROM findings WHERE status = 'unresolved'").fetchall()
        if (r["inode"], r["generation"]) in unready_sources
      }
      urt = {
        r["id"]
        for r in conn.execute("SELECT id, inode, generation FROM rule_tags WHERE verdict = 'violated'").fetchall()
        if (r["inode"], r["generation"]) in unready_sources
      }
      return uf, urt

  def test_joint_p1_to_p5_bounded_interleaving_patrol(self):
    consume_mod = load_consume_module()
    witnesses = {"consume_rounds": 0, "merged_pairs": 0, "out_of_order": 0}

    for seed in range(4):
      with self.subTest(seed=seed):
        self.tearDown()
        self.setUp()
        rng = random.Random(20261115 + seed * 101)
        mode = "quality"
        self.set_bg_mode(mode)
        p_a = self.source("work/a.jsonl", f"PATROL_A_0_{seed}")
        p_b = self.source("work/b.jsonl", f"PATROL_B_0_{seed}")
        behavior = {"primary_code": 200, "shadow_code": 200}
        step_counter = {"n": 0}

        def reply(body):
          step_counter["n"] += 1
          model = json.loads(body)["model"]
          user_txt = _dual_mod.fixtures.user_text(body)
          if model == "bg-audit-primary":
            if behavior["primary_code"] != 200:
              return '{"error": "primary-402"}', behavior["primary_code"]
            marker = "PATROL_A_" if "PATROL_A_" in user_txt else "PATROL_B_"
            quote = next((ln for ln in user_txt.splitlines() if marker in ln), user_txt.splitlines()[0])
            f_item = _dual_mod.fixtures.finding(marker, target="hook:shared", observation=f"p-obs-{step_counter['n']}")
            f_item["evidence"][0]["quote"] = quote[:40]
            # Extract line number from header in user_txt
            import re as _re
            m_ln = _re.search(r"\[line=(\d+)\b", user_txt)
            f_item["evidence"][0]["source_line"] = int(m_ln.group(1)) if m_ln else 1
            label = _dual_mod.fixtures.label_for(body, "BASE_RULE_FALLBACK")
            tags = [{"rule": label, "verdict": "violated", "source_line": f_item["evidence"][0]["source_line"], "quote": quote[:40]}] if label else []
            return _dual_mod.fixtures.analysis([f_item], rule_tags=tags), 200
          if model == "bg-audit-shadow":
            if behavior["shadow_code"] != 200:
              return '{"error": "shadow-402"}', behavior["shadow_code"]
            marker = "PATROL_A_" if "PATROL_A_" in user_txt else "PATROL_B_"
            quote = next((ln for ln in user_txt.splitlines() if marker in ln), user_txt.splitlines()[0])
            f_item = _dual_mod.fixtures.finding(marker, target="  Hook:Shared ", observation=f"s-obs-{step_counter['n']}")
            f_item["evidence"][0]["quote"] = quote[:40]
            import re as _re
            m_ln = _re.search(r"\[line=(\d+)\b", user_txt)
            f_item["evidence"][0]["source_line"] = int(m_ln.group(1)) if m_ln else 1
            return _dual_mod.fixtures.analysis([f_item]), 200
          return _dual_mod.fixtures.analysis([], rule_tags=[]), 200

        _, url = self.provider(reply)
        actions = [
          "append_a",
          "append_b",
          "primary_yield_shadow_ok",
          "primary_ok_shadow_402",
          "consume_step",
          "toggle_mode",
        ]
        seq = list(actions) + [rng.choice(actions) for _ in range(6)]
        rng.shuffle(seq)

        consumed_f_rounds = []
        unready_f_rounds = []
        consumed_rt_rounds = []
        unready_rt_rounds = []

        for step_idx, act in enumerate(seq):
          if act == "append_a":
            with p_a.open("a") as fh:
              fh.write(_dual_mod.fixtures.user_line(f"PATROL_A_{step_idx}_{seed}", session_id=p_a.stem))
            continue
          if act == "append_b":
            with p_b.open("a") as fh:
              fh.write(_dual_mod.fixtures.user_line(f"PATROL_B_{step_idx}_{seed}", session_id=p_b.stem))
            continue
          if act == "toggle_mode":
            mode = "eco" if mode == "quality" else "quality"
            self.set_bg_mode(mode)
            continue

          before = self.state_snapshot()
          if not before["sources"]:
            self.assertEqual(self.cli(["scan", *self.flags(url)]).returncode, 0)
            before = self.state_snapshot()
          incomplete_before = {r["inode"] for r in before["sources"] if r["status"] != "complete"}

          if act == "primary_yield_shadow_ok":
            prev = mode
            self.set_bg_mode("quality")
            self.set_concurrency(active=[{"agent": "claude-code", "model": "gemini-4-argon"}])
            with sqlite3.connect(self.state / "state.sqlite") as conn:
              conn.execute("DELETE FROM leg_pauses WHERE alias = 'bg-audit-shadow'")
              conn.commit()
            behavior["shadow_code"] = 200
            self.assertEqual(self.cli(["run", *self.dual_flags(url)], timeout=30).returncode, 0)
            after = self.state_snapshot()
            ok2, msg2 = _dual_mod.oracle_p2_primary_isolated(before, after)
            self.assertTrue(ok2, msg2)
            ok3, msg3 = _dual_mod.oracle_p3_yield_no_false_complete(before, after, incomplete_before, yielded=True)
            self.assertTrue(ok3, msg3)
            self.set_bg_mode(prev)
          elif act == "primary_ok_shadow_402":
            prev = mode
            self.set_bg_mode("quality")
            self.set_concurrency(active=[])
            with sqlite3.connect(self.state / "state.sqlite") as conn:
              conn.execute("DELETE FROM leg_pauses WHERE alias = 'bg-audit-primary'")
              conn.commit()
            behavior["primary_code"] = 200
            behavior["shadow_code"] = 402
            self.assertEqual(self.cli(["run", *self.dual_flags(url)], timeout=30).returncode, 0)
            self.set_bg_mode(prev)
          elif act == "consume_step":
            uf, urt = self._unready_ids()
            manifest, f_ids, rt_ids = self._consume_round()
            consumed_f_rounds.append(f_ids)
            unready_f_rounds.append(uf)
            consumed_rt_rounds.append(rt_ids)
            unready_rt_rounds.append(urt)
            witnesses["consume_rounds"] += 1
            if uf and f_ids and min(uf) < max(f_ids):
              witnesses["out_of_order"] += 1
            if manifest["raw_finding_rows"] > manifest["dedup_finding_count"]:
              witnesses["merged_pairs"] += 1

        # Final drain in quality mode + final consume
        self.set_bg_mode("quality")
        self.set_concurrency(active=[])
        with sqlite3.connect(self.state / "state.sqlite") as conn:
          conn.execute("DELETE FROM leg_pauses")
          conn.commit()
        behavior["primary_code"] = 200
        behavior["shadow_code"] = 200
        self.assertEqual(self.cli(["run", *self.dual_flags(url)], timeout=30).returncode, 0)

        uf_final, urt_final = self._unready_ids()
        self.assertEqual(uf_final, set())
        self.assertEqual(urt_final, set())
        manifest_final, f_final, rt_final = self._consume_round()
        consumed_f_rounds.append(f_final)
        unready_f_rounds.append(uf_final)
        consumed_rt_rounds.append(rt_final)
        unready_rt_rounds.append(urt_final)
        if manifest_final["raw_finding_rows"] > manifest_final["dedup_finding_count"]:
          witnesses["merged_pairs"] += 1

        # Verify all findings and rule_tags in DB were consumed exactly once (P1)
        with sqlite3.connect(self.state / "state.sqlite") as conn:
          all_f = [r[0] for r in conn.execute("SELECT id FROM findings WHERE status = 'unresolved' ORDER BY id").fetchall()]
          all_rt = [r[0] for r in conn.execute("SELECT id FROM rule_tags WHERE verdict = 'violated' ORDER BY id").fetchall()]
          unconsumed_f = [r[0] for r in conn.execute("SELECT id FROM findings WHERE status = 'unresolved' AND consumed_at IS NULL").fetchall()]
          unconsumed_rt = [r[0] for r in conn.execute("SELECT id FROM rule_tags WHERE verdict = 'violated' AND consumed_at IS NULL").fetchall()]
          # Check P4/P5 on the collected findings list directly as well
          all_f_dicts = [
            {
              "id": r[0], "inode": r[1], "generation": r[2], "lane": r[3],
              "target": r[4], "source_line": r[5],
            }
            for r in conn.execute("SELECT id, inode, generation, lane, target, source_line FROM findings WHERE status = 'unresolved'").fetchall()
          ]
        self.assertEqual(unconsumed_f, [])
        self.assertEqual(unconsumed_rt, [])
        ok1, msg1 = oracle_p1_consume_once(
          consumed_f_rounds, all_f, unready_f_rounds,
          consumed_rt_rounds, all_rt, unready_rt_rounds,
        )
        self.assertTrue(ok1, f"seed={seed}: {msg1}")
        ok4, msg4 = oracle_p4_shuffle_and_idempotent(consume_mod.premerge_cross_lane_findings, all_f_dicts, rng)
        self.assertTrue(ok4, f"seed={seed}: {msg4}")
        ok5, msg5 = oracle_p5_coverage_and_window(all_f_dicts, consume_mod.premerge_cross_lane_findings(all_f_dicts))
        self.assertTrue(ok5, f"seed={seed}: {msg5}")

    self.assertGreater(witnesses["consume_rounds"], 0)


if __name__ == "__main__":
  suite = unittest.TestSuite()
  for cls in (SessionAuditConsumeTest, RowLevelConsumeAndPremergeTest, JointP1ToP5PatrolTest):
    for name in cls.__dict__:
      if name.startswith("test_"):
        suite.addTest(cls(name))
  result = unittest.TextTestRunner(verbosity=2).run(suite)
  raise SystemExit(0 if result.wasSuccessful() else 1)
