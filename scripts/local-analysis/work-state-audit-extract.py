#!/usr/bin/env python3
"""Deterministic extractor for weekly-tue work-state-audit channel (read-only).

Reads claude-mem's SQLite work_state_entries table using the exact folding rules
of WorkStateRenderer.ts (case-insensitive project grouping, latest key wins,
closing a list hides list-level state while keeping open tasks visible) and emits
open lists with zero-LLM structural signals for downstream fact verification.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

DEFAULT_DB = Path.home() / ".claude-mem" / "claude-mem.db"
CLOSED_STATUSES = frozenset({"done", "dropped"})
WORK_STATE_CHAR_LIMIT = 3000

ISO_DATE_RE = re.compile(r"\b(202\d-\d{2}-\d{2})\b")
# Shorthand M/D or MM/DD only counts as a date when written with 2-digit month/day (12/10, 01/08)
# or adjacent to explicit date cues, avoiding score/progress fractions like "2/8 滿分" or "3/5".
MD_DATE_RE = re.compile(r"(?<![0-9/])(\d{1,2})/(\d{1,2})(?![0-9/])")
DATE_CUE_RE = re.compile(r"(?:續訂|到期|截止|重置|開賽|review|due|deadline|before|until|前|號|日)", re.IGNORECASE)


def extract_future_dates(text: str, today: date) -> list[str]:
  found: set[str] = set()
  today_iso = today.isoformat()

  for match in ISO_DATE_RE.findall(text):
    try:
      parsed = date.fromisoformat(match)
    except ValueError:
      continue
    if parsed.isoformat() > today_iso:
      found.add(parsed.isoformat())

  for m in MD_DATE_RE.finditer(text):
    m_str, d_str = m.group(1), m.group(2)
    month, day = int(m_str), int(d_str)
    if not (1 <= month <= 12 and 1 <= day <= 31):
      continue
    if month == day and f"{m_str}/{d_str}" in ("1/1", "12/12"):
      continue
    ctx_window = text[max(0, m.start() - 16):min(len(text), m.end() + 16)]
    is_two_digit_md = len(m_str) == 2 and len(d_str) == 2
    if not is_two_digit_md and not DATE_CUE_RE.search(ctx_window):
      continue
    candidate_year = today.year
    try:
      cand = date(candidate_year, month, day)
    except ValueError:
      continue
    if cand <= today:
      try:
        next_year_cand = date(candidate_year + 1, month, day)
      except ValueError:
        continue
      delta = (next_year_cand - today).days
      if 1 <= delta <= 90:
        found.add(f"{month:02d}/{day:02d}")
    else:
      delta = (cand - today).days
      if 1 <= delta <= 180:
        found.add(f"{month:02d}/{day:02d}")

  return sorted(found)


def estimate_rendered_chars(list_name: str, state_clean: dict, state_open: bool, open_tasks: dict) -> int:
  lines: list[str] = []
  if state_open:
    kvs = ", ".join(f"{k}={v}" for k, v in state_clean.items() if k != "task" and v is not None)
    lines.append(f"- {list_name}: {kvs}, updated X ago")
  else:
    lines.append(f"- {list_name}")
  for tname, tfields in open_tasks.items():
    st = tfields.get("status")
    st_prefix = f"[{st}] " if st not in (None, "") else ""
    kvs = ", ".join(f"{k}={v}" for k, v in tfields.items() if k not in ("task", "status") and v is not None)
    lines.append(f"  - {st_prefix}{tname}{f' ({kvs})' if kvs else ''}, updated X ago")
  return sum(len(line) + 1 for line in lines)


def extract_open_work_states(db_path: Path, today: date, now_epoch_ms: int) -> dict:
  if not db_path.exists():
    return {
      "today": today.isoformat(),
      "db_path": str(db_path),
      "db_exists": False,
      "total_lists": 0,
      "open_count": 0,
      "projects": {},
      "items": [],
    }

  uri = f"file:{db_path}?mode=ro"
  conn = sqlite3.connect(uri, uri=True, timeout=5.0)
  try:
    tables = {
      row[0]
      for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "work_state_entries" not in tables:
      return {
        "today": today.isoformat(),
        "db_path": str(db_path),
        "db_exists": True,
        "total_lists": 0,
        "open_count": 0,
        "projects": {},
        "items": [],
      }
    rows = conn.execute(
      "SELECT id, project, list_name, fields, created_at, created_at_epoch "
      "FROM work_state_entries ORDER BY id"
    ).fetchall()
  finally:
    conn.close()

  grouped: dict[tuple[str, str], list] = defaultdict(list)
  for row in rows:
    grouped[(row[1].lower(), row[2])].append(row)

  items: list[dict] = []
  proj_stats: dict[str, dict] = {}

  for (_proj_lc, list_name), ents in grouped.items():
    ents.sort(key=lambda r: r[0])
    project = ents[-1][1]
    last_epoch_ms = ents[-1][5]
    last_created_at = ents[-1][4]
    state: dict = {}
    tasks: dict[str, dict] = {}

    for _id, _p, _ln, fjson, _ca, _ep in ents:
      try:
        parsed = json.loads(fjson)
      except json.JSONDecodeError:
        continue
      if not isinstance(parsed, dict):
        continue
      t = parsed.get("task")
      if t is None or t == "":
        state.update(parsed)
      else:
        tkey = str(t)
        tasks[tkey] = {**tasks.get(tkey, {}), **parsed}

    open_tasks = {
      k: v for k, v in tasks.items()
      if str(v.get("status", "")).lower() not in CLOSED_STATUSES
    }
    state_clean = {k: v for k, v in state.items() if k != "task" and v is not None}
    state_status = str(state.get("status", "")).lower()
    state_open = bool(state_clean) and state_status not in CLOSED_STATUSES

    if not (state_open or open_tasks):
      continue

    age_days = round(max(0.0, (now_epoch_ms - last_epoch_ms) / 86400000.0), 1)
    signals: list[str] = []
    if state_status in CLOSED_STATUSES and open_tasks:
      signals.append(f"list_already_{state_status}_with_orphan_tasks({len(open_tasks)})")

    wt = state_clean.get("worktree")
    if isinstance(wt, str) and wt.startswith("/"):
      if not os.path.exists(wt):
        signals.append(f"worktree_missing:{wt}")
      else:
        signals.append(f"worktree_exists:{wt}")

    payload_blob = json.dumps(
      {"list": list_name, "state": state_clean if state_open else {}, "tasks": open_tasks},
      ensure_ascii=False,
    )
    future_dates = extract_future_dates(payload_blob, today)
    if future_dates:
      signals.append(f"future_date_guard:{','.join(future_dates)}")

    rendered_chars = estimate_rendered_chars(list_name, state_clean, state_open, open_tasks)
    pstat = proj_stats.setdefault(project, {"open_lists": 0, "estimated_chars": 0, "overflow_3k": False})
    pstat["open_lists"] += 1
    pstat["estimated_chars"] += rendered_chars

    progress = str(state_clean.get("progress") or state_clean.get("goal") or state_clean.get("next") or "")
    progress = progress.replace("\n", " ").strip()[:240]

    items.append({
      "project": project,
      "list_name": list_name,
      "age_days": age_days,
      "last_updated": last_created_at,
      "state_open": state_open,
      "state_status": state_status or None,
      "open_task_count": len(open_tasks),
      "open_tasks": [
        {
          "task": k,
          "status": v.get("status"),
          "note": str(v.get("detail") or v.get("note") or v.get("progress") or v.get("gate") or v.get("blocked_reason") or "")[:200],
        }
        for k, v in open_tasks.items()
      ],
      "worktree": wt if isinstance(wt, str) else None,
      "future_dates": future_dates,
      "signals": signals,
      "progress_excerpt": progress,
      "estimated_chars": rendered_chars,
    })

  # Compute 3K overflow per project (rule header is ~670 chars)
  rule_overhead = 700
  for pstat in proj_stats.values():
    pstat["overflow_3k"] = (pstat["estimated_chars"] + rule_overhead) > WORK_STATE_CHAR_LIMIT

  items.sort(key=lambda x: (-x["age_days"], x["project"].lower(), x["list_name"]))

  return {
    "today": today.isoformat(),
    "db_path": str(db_path),
    "db_exists": True,
    "total_lists": len(grouped),
    "open_count": len(items),
    "projects": dict(sorted(proj_stats.items(), key=lambda kv: (-kv[1]["open_lists"], kv[0].lower()))),
    "items": items,
  }


def format_markdown(report: dict) -> str:
  if report["open_count"] == 0:
    return f"OPEN_COUNT=0 (total_lists={report['total_lists']}, today={report['today']})\n"
  lines = [
    f"OPEN_COUNT={report['open_count']} (total_lists={report['total_lists']}, today={report['today']})",
    "",
    "### 各專案佔用與 3,000 字元摺疊風險",
    "| 專案 | 開放清單數 | 估算字元（含規則頭） | 會摺疊？ |",
    "|---|---:|---:|---|",
  ]
  for proj, st in report["projects"].items():
    flag = "⚠️ 是" if st["overflow_3k"] else "否"
    lines.append(f"| `{proj}` | {st['open_lists']} | {st['estimated_chars'] + 700} | {flag} |")
  lines.extend([
    "",
    "### 全機開放清單（依閒置天數降冪；天數僅供參考，判定一律看結案事實）",
    "| # | 閒置 | 專案 | 清單 | 免費確定訊號 | 摘要 |",
    "|---:|---:|---|---|---|---|",
  ])
  for idx, item in enumerate(report["items"], 1):
    sig = "; ".join(item["signals"]) if item["signals"] else "-"
    excerpt = item["progress_excerpt"]
    if not excerpt and item["open_tasks"]:
      excerpt = f"[{item['open_tasks'][0].get('status') or ''}] {item['open_tasks'][0]['task']}"
    excerpt = excerpt[:120].replace("|", "\\|")
    lines.append(f"| {idx} | {item['age_days']}d | `{item['project']}` | `{item['list_name']}` | {sig} | {excerpt} |")
  return "\n".join(lines) + "\n"


def main() -> int:
  parser = argparse.ArgumentParser(description="Extract open claude-mem work_state lists for fact-based audit.")
  parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Path to claude-mem.db")
  parser.add_argument("--today", type=str, default=None, help="Override YYYY-MM-DD for testing")
  parser.add_argument("--format", choices=("json", "md"), default="json")
  args = parser.parse_args()

  if args.today:
    today = date.fromisoformat(args.today)
    now_ms = int(datetime(today.year, today.month, today.day, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
  else:
    now_dt = datetime.now(timezone.utc)
    today = now_dt.date()
    now_ms = int(now_dt.timestamp() * 1000)

  report = extract_open_work_states(args.db, today, now_ms)
  if args.format == "json":
    print(json.dumps(report, ensure_ascii=False, indent=2))
  else:
    sys.stdout.write(format_markdown(report))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
