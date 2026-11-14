#!/usr/bin/env python3
"""session-audit 無門檻全量消費準備器與 ID 水位線（checkpoint）管理。

為什麼用自增 ID 當水位線而非時間窗：
- 若每次跑「過去 7 天」，不到 7 天跑兩次會重疊、超過 7 天沒跑會漏窗；
- 重疊視窗會讓使用者已拍板「不處理／已修好」的舊發現再次進場消耗注意力。
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile


CURSOR_FILENAME = "consume-cursor.json"
PENDING_FILENAME = "pending-consume.json"
DEFAULT_MAX_BATCH_BYTES = 60_000
DEFAULT_LEDGER = Path(__file__).resolve().parents[2] / "reports" / "local-analysis" / "pending-actions.jsonl"
RECONCILE_SCRIPT = Path(__file__).with_name("ledger-reconcile.py")
EMBEDDED_FINDINGS_RE = re.compile(r"<!--\s*session-audit-ledger-findings:\s*(\[.*?\])\s*-->", re.DOTALL)
REPORT_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})-session-audit-consume\.md$")
PRETOOLUSE_BLOCK_RE = re.compile(
  r"(?:^|\n)\s*(?:(?:Hook\s+)?PreToolUse:[^\n]*hook error:|<tool_use_error>Blocked:)",
)


def write_json_atomic(target: Path, payload: dict) -> None:
  tmp_path = target.with_name(f".{target.name}.tmp")
  tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
  os.replace(tmp_path, target)


def load_cursor(state_dir: Path) -> dict:
  cursor_path = state_dir / CURSOR_FILENAME
  if not cursor_path.is_file():
    return {
      "last_finding_id": 0,
      "last_rule_tag_id": 0,
      "updated_at": None,
      "history": [],
    }
  return json.loads(cursor_path.read_text(encoding="utf-8"))


def save_cursor(state_dir: Path, finding_id: int, rule_tag_id: int, report_path: str, note: str = "") -> dict:
  cursor = load_cursor(state_dir)
  prev_f = int(cursor.get("last_finding_id") or 0)
  prev_r = int(cursor.get("last_rule_tag_id") or 0)
  now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
  entry = {
    "consumed_at": now_iso,
    "from_finding_id": prev_f,
    "to_finding_id": finding_id,
    "from_rule_tag_id": prev_r,
    "to_rule_tag_id": rule_tag_id,
    "report_path": report_path,
    "note": note,
  }
  history = list(cursor.get("history") or [])
  history.append(entry)
  updated = {
    "last_finding_id": max(prev_f, finding_id),
    "last_rule_tag_id": max(prev_r, rule_tag_id),
    "updated_at": now_iso,
    "consumed_migrated": True,
    "history": history,
  }
  write_json_atomic(state_dir / CURSOR_FILENAME, updated)
  return updated


def load_rule_why_map(state_dir: Path) -> dict:
  wf_path = state_dir / "friction" / "workflow-general.md"
  rule_why = {}
  if not wf_path.is_file():
    return rule_why
  for line in wf_path.read_text(encoding="utf-8", errors="replace").splitlines():
    if line.startswith("- ") and "source_ref=rule:" in line:
      m_id = re.search(r"@([0-9a-f]{16})", line)
      m_why = re.search(r'why="([^"]+)"', line)
      if m_id and m_why:
        rule_why[m_id.group(1)] = m_why.group(1)
  return rule_why


def one_line(text: str, limit: int = 300) -> str:
  clean = (text or "").replace("\n", " ").replace("\r", " ").strip()
  return clean[:limit]


def table_columns(cur: sqlite3.Cursor, table_name: str) -> set:
  rows = cur.execute(f"PRAGMA table_info({table_name})").fetchall()
  return {row[1] for row in rows}


def table_exists(cur: sqlite3.Cursor, table_name: str) -> bool:
  row = cur.execute(
    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
    (table_name,),
  ).fetchone()
  return row is not None


def read_bg_mode(path: str) -> str | None:
  if not path:
    return None
  try:
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return "quality"
  if isinstance(data, dict) and data.get("mode") == "eco":
    return "eco"
  return "quality"


def load_shadow_status_map(cur: sqlite3.Cursor) -> tuple[bool, dict]:
  if not table_exists(cur, "shadow_cursors"):
    return False, {}
  status_map = {}
  for inode, generation, status in cur.execute("SELECT inode, generation, status FROM shadow_cursors").fetchall():
    status_map.setdefault((inode, int(generation)), []).append(status)
  return True, status_map


def is_source_ready(inode: str, generation: int, has_shadow: bool, shadow_map: dict, bg_mode: str | None) -> bool:
  if not has_shadow:
    return True
  statuses = shadow_map.get((inode, int(generation)), [])
  if "pending" in statuses:
    return False
  if statuses:
    return all(st in ("complete", "partial") for st in statuses)
  if bg_mode == "quality":
    return False
  return True


def ensure_consume_schema(conn: sqlite3.Connection, state_dir: Path) -> None:
  cur = conn.cursor()
  changed = False
  if table_exists(cur, "findings") and "consumed_at" not in table_columns(cur, "findings"):
    conn.execute("ALTER TABLE findings ADD COLUMN consumed_at TEXT DEFAULT NULL")
    changed = True
  if table_exists(cur, "rule_tags") and "consumed_at" not in table_columns(cur, "rule_tags"):
    conn.execute("ALTER TABLE rule_tags ADD COLUMN consumed_at TEXT DEFAULT NULL")
    changed = True

  cursor = load_cursor(state_dir)
  legacy_f = int(cursor.get("last_finding_id") or 0)
  legacy_r = int(cursor.get("last_rule_tag_id") or 0)
  if not cursor.get("consumed_migrated") and (legacy_f > 0 or legacy_r > 0):
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    has_shadow, shadow_map = load_shadow_status_map(cur)
    ready_sources = {
      (ino, int(gen))
      for ino, gen in cur.execute(
        "SELECT inode, generation FROM sources WHERE status IN ('complete', 'partial')"
      ).fetchall()
      if is_source_ready(ino, gen, has_shadow, shadow_map, None)
    }
    if legacy_f > 0 and table_exists(cur, "findings"):
      cand_f = cur.execute(
        "SELECT id, inode, generation FROM findings WHERE consumed_at IS NULL AND id <= ?",
        (legacy_f,),
      ).fetchall()
      stamp_f = [fid for fid, ino, gen in cand_f if (ino, int(gen)) in ready_sources]
      for i in range(0, len(stamp_f), 500):
        chunk = stamp_f[i : i + 500]
        marks = ",".join("?" for _ in chunk)
        conn.execute(f"UPDATE findings SET consumed_at = ? WHERE id IN ({marks}) AND consumed_at IS NULL", (now_iso, *chunk))
    if legacy_r > 0 and table_exists(cur, "rule_tags"):
      cand_rt = cur.execute(
        "SELECT id, inode, generation FROM rule_tags WHERE consumed_at IS NULL AND id <= ?",
        (legacy_r,),
      ).fetchall()
      stamp_rt = [rtid for rtid, ino, gen in cand_rt if (ino, int(gen)) in ready_sources]
      for i in range(0, len(stamp_rt), 500):
        chunk = stamp_rt[i : i + 500]
        marks = ",".join("?" for _ in chunk)
        conn.execute(f"UPDATE rule_tags SET consumed_at = ? WHERE id IN ({marks}) AND consumed_at IS NULL", (now_iso, *chunk))
    changed = True
    cursor["consumed_migrated"] = True
    write_json_atomic(state_dir / CURSOR_FILENAME, cursor)

  if changed:
    conn.commit()


def _normalize_premerge_key(item: dict) -> str | None:
  raw_target = " ".join(str(item.get("target") or "").strip().lower().split())
  if raw_target:
    return f"t:{raw_target}"
  raw_cat = " ".join(str(item.get("category") or "").strip().lower().split())
  if raw_cat:
    return f"c:{raw_cat}"
  return None


def premerge_cross_lane_findings(items: list) -> list:
  if not items:
    return []

  def item_sort_key(it: dict):
    inode = str(it.get("inode") or "")
    gen = int(it.get("generation") or 0)
    key = _normalize_premerge_key(it) or ""
    line = int(it.get("source_line") or 0)
    lane = str(it.get("lane") or "primary")
    fid = int(it.get("id") or 0)
    return (inode, gen, key, line, 0 if lane == "primary" else 1, lane, fid)

  sorted_items = sorted((dict(it) for it in items), key=item_sort_key)

  def collect_raw_ids(cluster: list) -> list:
    ids = set()
    for member in cluster:
      rids = member.get("raw_ids")
      if isinstance(rids, list) and rids:
        ids.update(rids)
      elif "id" in member:
        ids.add(member["id"])
    return sorted(ids)

  merged = []
  n = len(sorted_items)
  idx = 0
  while idx < n:
    curr = sorted_items[idx]
    key = _normalize_premerge_key(curr)
    if key is None:
      curr["raw_ids"] = collect_raw_ids([curr])
      merged.append(curr)
      idx += 1
      continue

    group_id = (str(curr.get("inode") or ""), int(curr.get("generation") or 0), key)
    group_end = idx + 1
    while group_end < n:
      nxt = sorted_items[group_end]
      nxt_gid = (str(nxt.get("inode") or ""), int(nxt.get("generation") or 0), _normalize_premerge_key(nxt))
      if nxt_gid != group_id:
        break
      group_end += 1

    pos = idx
    while pos < group_end:
      anchor = sorted_items[pos]
      anchor_line = int(anchor.get("source_line") or 0)
      anchor_lane = str(anchor.get("lane") or "primary")
      win_end = pos + 1
      has_cross_lane = False
      while win_end < group_end:
        cand = sorted_items[win_end]
        if int(cand.get("source_line") or 0) - anchor_line > 15:
          break
        if str(cand.get("lane") or "primary") != anchor_lane:
          has_cross_lane = True
        win_end += 1

      if not has_cross_lane:
        anchor["raw_ids"] = collect_raw_ids([anchor])
        merged.append(anchor)
        pos += 1
      else:
        cluster = sorted_items[pos:win_end]
        rep = dict(cluster[0])
        rep["raw_ids"] = collect_raw_ids(cluster)
        merged.append(rep)
        pos = win_end

    idx = group_end

  merged.sort(
    key=lambda it: (
      int(it.get("id") or 0),
      str(it.get("inode") or ""),
      int(it.get("generation") or 0),
      int(it.get("source_line") or 0),
      str(it.get("lane") or "primary"),
    )
  )
  return merged


def render_finding_entry(item: dict) -> str:
  return (
    f"## F{item['id']} | target={item['target']} | cls={item['cls']} | src={item['source']}\n"
    f"- observation: {item['observation']}\n"
    f"- check: {item['check_direction']}\n"
    f"- quote: {item['quote']}\n"
  )


def render_batch_markdown(batch_id: str, chunk: list) -> str:
  lines = [
    f"# Session-Audit Findings Batch {batch_id}",
    f"- finding_count: {len(chunk)}",
    f"- id_range: {chunk[0]['id']}..{chunk[-1]['id']}",
    "",
  ]
  for item in chunk:
    lines.append(render_finding_entry(item))
  return "\n".join(lines)


def slice_finding_batches(
  dedup_findings: list,
  batch_size: int,
  max_batch_bytes: int = DEFAULT_MAX_BATCH_BYTES,
) -> list:
  item_cap = max(1, int(batch_size or 280))
  byte_cap = max(1, int(max_batch_bytes or DEFAULT_MAX_BATCH_BYTES))
  sliced = []
  current_chunk = []
  entries_utf8_sum = 0

  def flush_current():
    nonlocal current_chunk, entries_utf8_sum
    if not current_chunk:
      return
    b_id = f"B{len(sliced) + 1:02d}"
    content = render_batch_markdown(b_id, current_chunk)
    utf8_len = len(content.encode("utf-8"))
    sliced.append((b_id, current_chunk, content, utf8_len))
    current_chunk = []
    entries_utf8_sum = 0

  for item in dedup_findings:
    entry_utf8_len = len(render_finding_entry(item).encode("utf-8"))
    if not current_chunk:
      current_chunk = [item]
      entries_utf8_sum = entry_utf8_len
      continue
    next_b_id = f"B{len(sliced) + 1:02d}"
    next_count = len(current_chunk) + 1
    header_prefix = (
      f"# Session-Audit Findings Batch {next_b_id}\n"
      f"- finding_count: {next_count}\n"
      f"- id_range: {current_chunk[0]['id']}..{item['id']}\n"
    )
    candidate_bytes = len(header_prefix.encode("utf-8")) + next_count + entries_utf8_sum + entry_utf8_len
    if len(current_chunk) >= item_cap or candidate_bytes > byte_cap:
      flush_current()
      current_chunk = [item]
      entries_utf8_sum = entry_utf8_len
    else:
      current_chunk.append(item)
      entries_utf8_sum += entry_utf8_len

  flush_current()
  return sliced


def extract_tool_result_error_text(raw_line: str, tool_use_id: str) -> str:
  try:
    obj = json.loads(raw_line)
  except (json.JSONDecodeError, ValueError):
    return ""
  if not isinstance(obj, dict):
    return ""
  msg = obj.get("message")
  content = msg.get("content") if isinstance(msg, dict) else None
  if not isinstance(content, list):
    return ""
  parts = []
  for blk in content:
    if not isinstance(blk, dict):
      continue
    if blk.get("type") != "tool_result" or blk.get("tool_use_id") != tool_use_id:
      continue
    if blk.get("is_error") is not True:
      continue
    val = blk.get("content")
    if isinstance(val, str):
      parts.append(val)
    elif isinstance(val, list):
      parts.extend(x.get("text", "") for x in val if isinstance(x, dict) and isinstance(x.get("text"), str))
  return "\n".join(parts)


def detect_pretooluse_gate_blocks(cur: sqlite3.Cursor, rt_rows: list) -> set:
  required_seg_cols = {"inode", "generation", "line", "byte_offset", "role", "tool_use_id"}
  has_segments = required_seg_cols.issubset(table_columns(cur, "segments"))
  line_block_cache = {}
  raw_line_cache = {}
  blocked_ids = set()

  def read_line_at(spath: str, byte_offset: int) -> str:
    cache_key = (spath, byte_offset)
    if cache_key in raw_line_cache:
      return raw_line_cache[cache_key]
    line_str = ""
    try:
      p = Path(spath)
      if p.is_file():
        with p.open("rb") as fh:
          fh.seek(byte_offset)
          line_str = fh.readline().decode("utf-8", errors="replace")
    except OSError:
      line_str = ""
    raw_line_cache[cache_key] = line_str
    return line_str

  for rtid, _rpath, _rhead, _rid, quote, _ctime, inode, _scls, gen, sline, spath in rt_rows:
    if PRETOOLUSE_BLOCK_RE.search(quote or ""):
      blocked_ids.add(rtid)
      continue
    if not has_segments or not sline or not spath:
      continue
    loc_key = (inode, gen, sline)
    if loc_key not in line_block_cache:
      is_blocked = False
      tu_rows = cur.execute(
        """
        SELECT DISTINCT tool_use_id
        FROM segments
        WHERE inode = ? AND generation = ? AND line = ? AND tool_use_id != '-'
        """,
        (inode, gen, sline),
      ).fetchall()
      for (tuid,) in tu_rows:
        tr_rows = cur.execute(
          """
          SELECT DISTINCT byte_offset
          FROM segments
          WHERE inode = ? AND generation = ? AND tool_use_id = ? AND role = 'user'
          """,
          (inode, gen, tuid),
        ).fetchall()
        for (boff,) in tr_rows:
          err_txt = extract_tool_result_error_text(read_line_at(spath, int(boff)), tuid)
          if err_txt and PRETOOLUSE_BLOCK_RE.search(err_txt):
            is_blocked = True
            break
        if is_blocked:
          break
      line_block_cache[loc_key] = is_blocked
    if line_block_cache[loc_key]:
      blocked_ids.add(rtid)

  return blocked_ids


def cmd_prepare(args: argparse.Namespace) -> int:
  state_dir = Path(args.state).expanduser().resolve()
  out_dir = Path(args.out_dir).expanduser().resolve()
  db_path = state_dir / "state.sqlite"
  if not db_path.is_file():
    print(json.dumps({"ok": False, "reason": "state-sqlite-missing", "path": str(db_path)}))
    return 1

  conn = sqlite3.connect(db_path)
  conn.execute("PRAGMA busy_timeout=30000")
  ensure_consume_schema(conn, state_dir)

  cursor = load_cursor(state_dir)
  min_finding_id = args.after_finding_id if args.after_finding_id is not None else int(cursor.get("last_finding_id") or 0)
  min_rule_tag_id = args.after_rule_tag_id if args.after_rule_tag_id is not None else int(cursor.get("last_rule_tag_id") or 0)
  since_mtime = float(args.since_mtime or 0.0)
  since_iso = args.since_iso or ""
  bg_mode = read_bg_mode(args.bg_mode_file)

  cur = conn.cursor()
  has_shadow, shadow_map = load_shadow_status_map(cur)
  f_cols = table_columns(cur, "findings")
  lane_expr = "COALESCE(NULLIF(f.lane, ''), 'primary')" if "lane" in f_cols else "'primary'"

  f_params = []
  after_f_clause = ""
  if args.after_finding_id is not None:
    after_f_clause = "AND f.id > ?"
    f_params.append(args.after_finding_id)
  f_params.append(since_mtime)

  raw_f_rows = cur.execute(
    f"""
    SELECT f.id, s.path, s.classification, f.source_line, f.target,
           f.observation, f.check_direction, f.quote,
           COALESCE(NULLIF(f.issue_ref, ''), f.inode || ':' || f.source_line) AS iref,
           f.inode, f.generation, {lane_expr} AS lane
    FROM findings f
    JOIN sources s ON s.inode = f.inode AND s.generation = f.generation
    WHERE s.included = 1
      AND s.status IN ('complete', 'partial')
      AND f.status = 'unresolved'
      AND f.consumed_at IS NULL
      {after_f_clause}
      AND s.mtime >= ?
    ORDER BY f.id ASC
    """,
    tuple(f_params),
  ).fetchall()

  f_rows = [
    row for row in raw_f_rows
    if is_source_ready(row[9], int(row[10]), has_shadow, shadow_map, bg_mode)
  ]

  iref_groups = {}
  dedup_order = []
  prepared_finding_ids = []
  max_finding_id = min_finding_id
  for row in f_rows:
    fid, spath, scls, sline, target, obs, direction, quote, iref, inode, gen, lane = row
    prepared_finding_ids.append(fid)
    if fid > max_finding_id:
      max_finding_id = fid
    gkey = (lane, iref)
    if gkey in iref_groups:
      iref_groups[gkey]["raw_ids"].append(fid)
      continue
    short_path = re.sub(r"^/Users/[^/]+/", "~/", spath or "")
    entry = {
      "id": fid,
      "inode": inode,
      "generation": int(gen),
      "lane": lane,
      "source_line": int(sline),
      "source": f"{short_path}:{sline}",
      "cls": scls,
      "target": one_line(target, 120),
      "observation": one_line(obs, 260),
      "check_direction": one_line(direction, 180),
      "quote": one_line(quote, 180),
      "raw_ids": [fid],
    }
    iref_groups[gkey] = entry
    dedup_order.append(entry)

  dedup_findings = premerge_cross_lane_findings(dedup_order)

  rt_cols = table_columns(cur, "rule_tags")
  rt_line_expr = "rt.source_line" if "source_line" in rt_cols else "0"
  rt_params = []
  after_rt_clause = ""
  if args.after_rule_tag_id is not None:
    after_rt_clause = "AND rt.id > ?"
    rt_params.append(args.after_rule_tag_id)
  rt_params.extend([since_iso, since_iso])

  raw_rt_rows = cur.execute(
    f"""
    SELECT rt.id, rt.rule_path, rt.rule_heading, rt.rule_id, rt.quote,
           rt.conversation_time, s.inode, s.classification,
           rt.generation, {rt_line_expr} AS source_line, s.path
    FROM rule_tags rt
    JOIN sources s ON s.inode = rt.inode AND s.generation = rt.generation
    WHERE s.included = 1
      AND s.status IN ('complete', 'partial')
      AND rt.verdict = 'violated'
      AND rt.consumed_at IS NULL
      {after_rt_clause}
      AND (? = '' OR rt.conversation_time >= ?)
    ORDER BY rt.id ASC
    """,
    tuple(rt_params),
  ).fetchall()
  rt_rows = [
    row for row in raw_rt_rows
    if is_source_ready(row[6], int(row[8]), has_shadow, shadow_map, bg_mode)
  ]
  blocked_rt_ids = detect_pretooluse_gate_blocks(cur, rt_rows)
  conn.close()

  prepared_rule_tag_ids = [row[0] for row in rt_rows]
  pending_payload = {
    "prepared_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "finding_ids": prepared_finding_ids,
    "rule_tag_ids": prepared_rule_tag_ids,
  }
  write_json_atomic(state_dir / PENDING_FILENAME, pending_payload)

  rule_why = load_rule_why_map(state_dir)
  max_rule_tag_id = min_rule_tag_id
  rule_groups = {}
  executed_total = 0
  blocked_total = 0
  for rtid, rpath, rhead, rid, quote, ctime, inode, scls, _gen, _sline, _spath in rt_rows:
    if rtid > max_rule_tag_id:
      max_rule_tag_id = rtid
    is_blocked = rtid in blocked_rt_ids
    if is_blocked:
      blocked_total += 1
    else:
      executed_total += 1
    why_text = rule_why.get(rid, f"{rpath} > {rhead} (@{rid[:8]})")
    key = why_text if rid in rule_why else rid
    grp = rule_groups.setdefault(key, {
      "rule_path": rpath,
      "rule_heading": rhead,
      "rule_ids": set(),
      "rule_text": why_text,
      "hits": 0,
      "executed_hits": 0,
      "gate_blocked_hits": 0,
      "inodes": set(),
      "main_inodes": set(),
      "executed_inodes": set(),
      "executed_main_inodes": set(),
      "executed_quotes": [],
      "blocked_quotes": [],
    })
    grp["rule_ids"].add(rid)
    grp["hits"] += 1
    grp["inodes"].add(inode)
    if scls == "human-main":
      grp["main_inodes"].add(inode)
    q_clean = one_line(quote, 140)
    if is_blocked:
      grp["gate_blocked_hits"] += 1
      if q_clean and len(grp["blocked_quotes"]) < 2 and q_clean not in grp["blocked_quotes"]:
        grp["blocked_quotes"].append(q_clean)
    else:
      grp["executed_hits"] += 1
      grp["executed_inodes"].add(inode)
      if scls == "human-main":
        grp["executed_main_inodes"].add(inode)
      if q_clean and len(grp["executed_quotes"]) < 3 and q_clean not in grp["executed_quotes"]:
        grp["executed_quotes"].append(q_clean)

  out_dir.mkdir(parents=True, exist_ok=True)
  batch_size = int(args.batch_size or 280)
  max_batch_bytes = int(getattr(args, "max_batch_bytes", None) or DEFAULT_MAX_BATCH_BYTES)
  batches = []
  for b_id, chunk, content, utf8_len in slice_finding_batches(dedup_findings, batch_size, max_batch_bytes):
    b_path = out_dir / f"findings-{b_id}.md"
    b_path.write_text(content, encoding="utf-8")
    batches.append({
      "batch_id": b_id,
      "path": str(b_path),
      "finding_count": len(chunk),
      "first_finding_id": chunk[0]["id"],
      "last_finding_id": chunk[-1]["id"],
      "chars": len(content),
      "bytes": utf8_len,
    })

  sorted_rules = sorted(
    rule_groups.values(),
    key=lambda g: (
      -len(g["executed_main_inodes"]),
      -len(g["executed_inodes"]),
      -g["executed_hits"],
      -len(g["main_inodes"]),
      -len(g["inodes"]),
      -g["hits"],
    ),
  )
  rt_range = f"{min(prepared_rule_tag_ids)}..{max(prepared_rule_tag_ids)}" if prepared_rule_tag_ids else f"{min_rule_tag_id + 1}..{max_rule_tag_id}"
  rules_md_lines = [
    "# Session-Audit Rule Violations Summary",
    f"- total_violation_tags: {len(rt_rows)}",
    f"- executed_violation_tags: {executed_total}",
    f"- gate_blocked_unexecuted_tags: {blocked_total}",
    f"- distinct_merged_rules: {len(sorted_rules)}",
    f"- rule_tag_id_range: {rt_range}",
    "- execution_note: gate_blocked 代表該工具呼叫已被 PreToolUse gate 當場攔下未實際執行（既有防線已生效），判讀時不得將已攔截嘗試當成無防護繞過缺口；優先審視 executed > 0 的規則。",
    "",
  ]
  for r_idx, g in enumerate(sorted_rules, 1):
    rids = ",".join(sorted(r[:8] for r in g["rule_ids"]))
    if g["gate_blocked_hits"] > 0 and g["executed_hits"] == 0:
      exec_status = f"all_blocked_by_pretooluse_gate（全數 {g['gate_blocked_hits']} 筆皆已被既有 PreToolUse gate 當場攔下未執行，非無防護繞過）"
    elif g["gate_blocked_hits"] > 0:
      exec_status = f"mixed（實際執行違規 {g['executed_hits']} 筆；另有 {g['gate_blocked_hits']} 筆已被既有 PreToolUse gate 當場攔下未執行）"
    else:
      exec_status = f"executed_only（{g['executed_hits']} 筆皆為實際執行）"
    quote_lines = [f"- sample_quote [executed]: {q}" for q in g["executed_quotes"]]
    quote_lines.extend(f"- sample_quote [blocked_by_pretooluse_gate]: {q}" for q in g["blocked_quotes"])
    rules_md_lines.append(
      f"## R{r_idx:02d} | main_sessions={len(g['main_inodes'])} (executed={len(g['executed_main_inodes'])}) | all_files={len(g['inodes'])} | hits={g['hits']} (executed={g['executed_hits']}, gate_blocked={g['gate_blocked_hits']}) | {g['rule_path']} > {g['rule_heading']} ({rids})\n"
      f"- rule_text: {g['rule_text']}\n"
      f"- execution_status: {exec_status}\n"
      + ("\n".join(quote_lines) + "\n" if quote_lines else "")
    )
  rules_path = out_dir / "rule-violations.md"
  rules_content = "\n".join(rules_md_lines)
  rules_path.write_text(rules_content, encoding="utf-8")

  manifest = {
    "ok": True,
    "state_dir": str(state_dir),
    "out_dir": str(out_dir),
    "cursor_before": {
      "last_finding_id": min_finding_id,
      "last_rule_tag_id": min_rule_tag_id,
    },
    "cursor_candidate": {
      "last_finding_id": max_finding_id,
      "last_rule_tag_id": max_rule_tag_id,
    },
    "raw_finding_rows": len(f_rows),
    "dedup_finding_count": len(dedup_findings),
    "batch_count": len(batches),
    "batches": batches,
    "rule_violation_rows": len(rt_rows),
    "executed_rule_violation_rows": executed_total,
    "gate_blocked_rule_violation_rows": blocked_total,
    "distinct_merged_rules": len(sorted_rules),
    "rules_summary_path": str(rules_path),
    "rules_summary_chars": len(rules_content),
  }
  manifest_path = out_dir / "manifest.json"
  manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
  print(json.dumps(manifest, ensure_ascii=False))
  return 0


def sanitize_source_key(raw_key: str, fallback_idx: int, date_str: str) -> str:
  cleaned = (raw_key or "").strip().lower()
  if not cleaned.startswith("session-audit-consume:"):
    cleaned = f"session-audit-consume:{cleaned}" if cleaned else f"session-audit-consume:{date_str}-{fallback_idx}"
  cleaned = re.sub(r"[^a-z0-9:._-]+", "-", cleaned).strip("-._:")
  if not re.match(r"^[a-z0-9]", cleaned):
    cleaned = f"s-{cleaned}"
  return cleaned[:128]


def extract_findings_from_report(report_path: str) -> list:
  if not report_path:
    return []
  p = Path(report_path).expanduser()
  if not p.is_file():
    raise ValueError(f"report file not found: {report_path}")
  text = p.read_text(encoding="utf-8", errors="replace")
  m = EMBEDDED_FINDINGS_RE.search(text)
  if not m:
    raise ValueError(f"missing embedded session-audit-ledger-findings marker in {report_path}")
  data = json.loads(m.group(1))
  if not isinstance(data, list):
    raise ValueError("embedded session-audit-ledger-findings must be a JSON array")
  return data


def ingest_findings_to_ledger(raw_items: list, ledger_path: Path, date_str: str) -> int:
  if not raw_items:
    return 0
  ledger_path.parent.mkdir(parents=True, exist_ok=True)
  if not ledger_path.exists():
    ledger_path.write_text("", encoding="utf-8")

  existing_by_title = {}
  existing_by_source_key = {}
  for line in ledger_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line:
      continue
    row = json.loads(line)
    title = row.get("title")
    skey = row.get("source_key")
    if title:
      existing_by_title[title] = row
    if skey:
      existing_by_source_key[skey] = row

  payload = []
  seen_keys = set()
  for idx, item in enumerate(raw_items, 1):
    if not isinstance(item, dict):
      continue
    title = (item.get("title") or "").strip()
    if not title:
      continue
    skey = sanitize_source_key(item.get("source_key") or "", idx, date_str)
    if skey in seen_keys:
      continue
    seen_keys.add(skey)
    note = (item.get("note") or "").strip()
    match_title = item.get("match")
    if not match_title:
      if skey in existing_by_source_key:
        match_title = existing_by_source_key[skey].get("title")
      elif title in existing_by_title:
        match_title = title
    payload.append({
      "title": title,
      "match": match_title,
      "channel": "session-audit-consume",
      "source_key": skey,
      "note": note,
    })

  if not payload:
    return 0

  with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as tf:
    json.dump(payload, tf, ensure_ascii=False, indent=2)
    tmp_json = tf.name
  try:
    proc = subprocess.run(
      [
        sys.executable,
        str(RECONCILE_SCRIPT),
        "--date",
        date_str,
        "--ledger",
        str(ledger_path),
        "--findings",
        tmp_json,
        "--apply",
        "--no-health",
      ],
      text=True,
      capture_output=True,
      check=False,
    )
    if proc.returncode != 0:
      sys.stderr.write(proc.stderr or proc.stdout or "ledger-reconcile failed\n")
      raise RuntimeError(f"ledger-reconcile exited with {proc.returncode}")
  finally:
    try:
      os.unlink(tmp_json)
    except FileNotFoundError:
      pass
  return len(payload)


def stamp_consumed_rows(state_dir: Path, finding_id: int, rule_tag_id: int, now_iso: str) -> None:
  db_path = state_dir / "state.sqlite"
  pending_path = state_dir / PENDING_FILENAME
  if not db_path.is_file() or not pending_path.is_file():
    return
  conn = sqlite3.connect(db_path)
  conn.execute("PRAGMA busy_timeout=30000")
  ensure_consume_schema(conn, state_dir)
  cur = conn.cursor()

  pending = json.loads(pending_path.read_text(encoding="utf-8"))
  f_all = [int(i) for i in pending.get("finding_ids") or []]
  r_all = [int(i) for i in pending.get("rule_tag_ids") or []]
  f_stamp = [i for i in f_all if i <= finding_id]
  r_stamp = [i for i in r_all if i <= rule_tag_id]
  if table_exists(cur, "findings"):
    for i in range(0, len(f_stamp), 500):
      chunk = f_stamp[i : i + 500]
      marks = ",".join("?" for _ in chunk)
      conn.execute(f"UPDATE findings SET consumed_at = ? WHERE id IN ({marks}) AND consumed_at IS NULL", (now_iso, *chunk))
  if table_exists(cur, "rule_tags"):
    for i in range(0, len(r_stamp), 500):
      chunk = r_stamp[i : i + 500]
      marks = ",".join("?" for _ in chunk)
      conn.execute(f"UPDATE rule_tags SET consumed_at = ? WHERE id IN ({marks}) AND consumed_at IS NULL", (now_iso, *chunk))
  f_rem = [i for i in f_all if i > finding_id]
  r_rem = [i for i in r_all if i > rule_tag_id]
  if f_rem or r_rem:
    write_json_atomic(pending_path, {"prepared_at": pending.get("prepared_at"), "finding_ids": f_rem, "rule_tag_ids": r_rem})
  else:
    pending_path.unlink(missing_ok=True)

  conn.commit()
  conn.close()


def cmd_commit(args: argparse.Namespace) -> int:
  state_dir = Path(args.state).expanduser().resolve()
  report_path = args.report or ""
  date_str = args.date or ""
  if not date_str and report_path:
    m_date = REPORT_DATE_RE.search(report_path)
    if m_date:
      date_str = m_date.group(1)
  if not date_str:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

  try:
    if args.findings_file:
      raw_items = json.loads(Path(args.findings_file).expanduser().read_text(encoding="utf-8"))
      if not isinstance(raw_items, list):
        raise ValueError("--findings-file must contain a JSON array")
    else:
      raw_items = extract_findings_from_report(report_path)
    ledger_path = Path(args.ledger).expanduser().resolve() if args.ledger else DEFAULT_LEDGER
    ingested_count = ingest_findings_to_ledger(raw_items, ledger_path, date_str)
  except Exception as exc:
    print(json.dumps({"ok": False, "reason": "ledger-ingest-failed", "error": str(exc)}, ensure_ascii=False))
    return 1

  now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
  stamp_consumed_rows(state_dir, int(args.finding_id), int(args.rule_tag_id), now_iso)
  updated = save_cursor(
    state_dir=state_dir,
    finding_id=int(args.finding_id),
    rule_tag_id=int(args.rule_tag_id),
    report_path=report_path,
    note=args.note or "",
  )
  print(json.dumps({"ok": True, "ingested_findings": ingested_count, "cursor": updated}, ensure_ascii=False))
  return 0


def main() -> int:
  parser = argparse.ArgumentParser(prog="session-audit-consume")
  sub = parser.add_subparsers(dest="cmd", required=True)

  p_prep = sub.add_parser("prepare")
  p_prep.add_argument("--state", required=True)
  p_prep.add_argument("--out-dir", required=True)
  p_prep.add_argument("--batch-size", type=int, default=280)
  p_prep.add_argument("--max-batch-bytes", type=int, default=DEFAULT_MAX_BATCH_BYTES)
  p_prep.add_argument("--since-mtime", type=float, default=0.0)
  p_prep.add_argument("--since-iso", default="")
  p_prep.add_argument("--after-finding-id", type=int, default=None)
  p_prep.add_argument("--after-rule-tag-id", type=int, default=None)
  p_prep.add_argument("--bg-mode-file", default="")

  p_commit = sub.add_parser("commit-cursor")
  p_commit.add_argument("--state", required=True)
  p_commit.add_argument("--finding-id", type=int, required=True)
  p_commit.add_argument("--rule-tag-id", type=int, required=True)
  p_commit.add_argument("--report", default="")
  p_commit.add_argument("--date", default="")
  p_commit.add_argument("--findings-file", default="")
  p_commit.add_argument("--ledger", default="")
  p_commit.add_argument("--note", default="")

  args = parser.parse_args()
  if args.cmd == "prepare":
    return cmd_prepare(args)
  if args.cmd == "commit-cursor":
    return cmd_commit(args)
  return 1


if __name__ == "__main__":
  sys.exit(main())
