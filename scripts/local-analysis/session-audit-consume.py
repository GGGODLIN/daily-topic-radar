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
import sys


CURSOR_FILENAME = "consume-cursor.json"


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
    "history": history,
  }
  cursor_path = state_dir / CURSOR_FILENAME
  tmp_path = state_dir / f".{CURSOR_FILENAME}.tmp"
  tmp_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
  os.replace(tmp_path, cursor_path)
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


def cmd_prepare(args: argparse.Namespace) -> int:
  state_dir = Path(args.state).expanduser().resolve()
  out_dir = Path(args.out_dir).expanduser().resolve()
  db_path = state_dir / "state.sqlite"
  if not db_path.is_file():
    print(json.dumps({"ok": False, "reason": "state-sqlite-missing", "path": str(db_path)}))
    return 1

  cursor = load_cursor(state_dir)
  min_finding_id = args.after_finding_id if args.after_finding_id is not None else int(cursor.get("last_finding_id") or 0)
  min_rule_tag_id = args.after_rule_tag_id if args.after_rule_tag_id is not None else int(cursor.get("last_rule_tag_id") or 0)
  since_mtime = float(args.since_mtime or 0.0)
  since_iso = args.since_iso or ""

  conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
  cur = conn.cursor()

  f_rows = cur.execute(
    """
    SELECT f.id, s.path, s.classification, f.source_line, f.target,
           f.observation, f.check_direction, f.quote,
           COALESCE(NULLIF(f.issue_ref, ''), f.inode || ':' || f.source_line) AS iref
    FROM findings f
    JOIN sources s ON s.inode = f.inode AND s.generation = f.generation
    WHERE s.included = 1
      AND s.status IN ('complete', 'partial')
      AND f.status = 'unresolved'
      AND f.id > ?
      AND s.mtime >= ?
    ORDER BY f.id ASC
    """,
    (min_finding_id, since_mtime),
  ).fetchall()

  seen_iref = set()
  dedup_findings = []
  max_finding_id = min_finding_id
  for row in f_rows:
    fid, spath, scls, sline, target, obs, direction, quote, iref = row
    if fid > max_finding_id:
      max_finding_id = fid
    if iref in seen_iref:
      continue
    seen_iref.add(iref)
    short_path = re.sub(r"^/Users/[^/]+/", "~/", spath or "")
    dedup_findings.append({
      "id": fid,
      "source": f"{short_path}:{sline}",
      "cls": scls,
      "target": one_line(target, 120),
      "observation": one_line(obs, 260),
      "check_direction": one_line(direction, 180),
      "quote": one_line(quote, 180),
    })

  rt_rows = cur.execute(
    """
    SELECT rt.id, rt.rule_path, rt.rule_heading, rt.rule_id, rt.quote,
           rt.conversation_time, s.inode, s.classification
    FROM rule_tags rt
    JOIN sources s ON s.inode = rt.inode AND s.generation = rt.generation
    WHERE s.included = 1
      AND s.status IN ('complete', 'partial')
      AND rt.verdict = 'violated'
      AND rt.id > ?
      AND (? = '' OR rt.conversation_time >= ?)
    ORDER BY rt.id ASC
    """,
    (min_rule_tag_id, since_iso, since_iso),
  ).fetchall()
  conn.close()

  rule_why = load_rule_why_map(state_dir)
  max_rule_tag_id = min_rule_tag_id
  rule_groups = {}
  for rtid, rpath, rhead, rid, quote, ctime, inode, scls in rt_rows:
    if rtid > max_rule_tag_id:
      max_rule_tag_id = rtid
    why_text = rule_why.get(rid, f"{rpath} > {rhead} (@{rid[:8]})")
    key = why_text if rid in rule_why else rid
    grp = rule_groups.setdefault(key, {
      "rule_path": rpath,
      "rule_heading": rhead,
      "rule_ids": set(),
      "rule_text": why_text,
      "hits": 0,
      "inodes": set(),
      "main_inodes": set(),
      "quotes": [],
    })
    grp["rule_ids"].add(rid)
    grp["hits"] += 1
    grp["inodes"].add(inode)
    if scls == "human-main":
      grp["main_inodes"].add(inode)
    q_clean = one_line(quote, 140)
    if q_clean and len(grp["quotes"]) < 3 and q_clean not in grp["quotes"]:
      grp["quotes"].append(q_clean)

  out_dir.mkdir(parents=True, exist_ok=True)
  batch_size = int(args.batch_size or 280)
  batches = []
  for idx in range(0, len(dedup_findings), batch_size):
    chunk = dedup_findings[idx:idx + batch_size]
    b_num = (idx // batch_size) + 1
    b_id = f"B{b_num:02d}"
    b_path = out_dir / f"findings-{b_id}.md"
    lines = [
      f"# Session-Audit Findings Batch {b_id}",
      f"- finding_count: {len(chunk)}",
      f"- id_range: {chunk[0]['id']}..{chunk[-1]['id']}",
      "",
    ]
    for item in chunk:
      lines.append(
        f"## F{item['id']} | target={item['target']} | cls={item['cls']} | src={item['source']}\n"
        f"- observation: {item['observation']}\n"
        f"- check: {item['check_direction']}\n"
        f"- quote: {item['quote']}\n"
      )
    content = "\n".join(lines)
    b_path.write_text(content, encoding="utf-8")
    batches.append({
      "batch_id": b_id,
      "path": str(b_path),
      "finding_count": len(chunk),
      "first_finding_id": chunk[0]["id"],
      "last_finding_id": chunk[-1]["id"],
      "chars": len(content),
    })

  sorted_rules = sorted(
    rule_groups.values(),
    key=lambda g: (-len(g["main_inodes"]), -len(g["inodes"]), -g["hits"]),
  )
  rules_md_lines = [
    "# Session-Audit Rule Violations Summary",
    f"- total_violation_tags: {len(rt_rows)}",
    f"- distinct_merged_rules: {len(sorted_rules)}",
    f"- rule_tag_id_range: {min_rule_tag_id + 1}..{max_rule_tag_id}",
    "",
  ]
  for r_idx, g in enumerate(sorted_rules, 1):
    rids = ",".join(sorted(r[:8] for r in g["rule_ids"]))
    rules_md_lines.append(
      f"## R{r_idx:02d} | main_sessions={len(g['main_inodes'])} | all_files={len(g['inodes'])} | hits={g['hits']} | {g['rule_path']} > {g['rule_heading']} ({rids})\n"
      f"- rule_text: {g['rule_text']}\n"
      + "\n".join(f"- sample_quote: {q}" for q in g["quotes"])
      + "\n"
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
    "distinct_merged_rules": len(sorted_rules),
    "rules_summary_path": str(rules_path),
    "rules_summary_chars": len(rules_content),
  }
  manifest_path = out_dir / "manifest.json"
  manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
  print(json.dumps(manifest, ensure_ascii=False))
  return 0


def cmd_commit(args: argparse.Namespace) -> int:
  state_dir = Path(args.state).expanduser().resolve()
  updated = save_cursor(
    state_dir=state_dir,
    finding_id=int(args.finding_id),
    rule_tag_id=int(args.rule_tag_id),
    report_path=args.report or "",
    note=args.note or "",
  )
  print(json.dumps({"ok": True, "cursor": updated}, ensure_ascii=False))
  return 0


def main() -> int:
  parser = argparse.ArgumentParser(prog="session-audit-consume")
  sub = parser.add_subparsers(dest="cmd", required=True)

  p_prep = sub.add_parser("prepare")
  p_prep.add_argument("--state", required=True)
  p_prep.add_argument("--out-dir", required=True)
  p_prep.add_argument("--batch-size", type=int, default=280)
  p_prep.add_argument("--since-mtime", type=float, default=0.0)
  p_prep.add_argument("--since-iso", default="")
  p_prep.add_argument("--after-finding-id", type=int, default=None)
  p_prep.add_argument("--after-rule-tag-id", type=int, default=None)

  p_commit = sub.add_parser("commit-cursor")
  p_commit.add_argument("--state", required=True)
  p_commit.add_argument("--finding-id", type=int, required=True)
  p_commit.add_argument("--rule-tag-id", type=int, required=True)
  p_commit.add_argument("--report", default="")
  p_commit.add_argument("--note", default="")

  args = parser.parse_args()
  if args.cmd == "prepare":
    return cmd_prepare(args)
  if args.cmd == "commit-cursor":
    return cmd_commit(args)
  return 1


if __name__ == "__main__":
  sys.exit(main())
