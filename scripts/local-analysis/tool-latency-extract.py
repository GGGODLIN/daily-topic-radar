#!/usr/bin/env python3
"""tool-latency-extract.py — 工具耗時週對週趨勢（2026-11-10 建，/daily-local 的 tool-latency channel）。

問題：harness 自己的腳本會隨 session 紀錄變多而越跑越慢，沒人會發現。2026-11-10 量到
/self-closeout 用的 closeout-provenance.py 每次中位數從約 4 秒爬到 12–14 秒，90 天跑了 3,860 次，
摩擦紀錄與 git 歷史都沒有任何一筆提到慢。

做法：唯讀掃主 session jsonl，tool_result 時間戳減 tool_use 時間戳，依「實際跑的腳本或程式」分組，
比較「執行日前 7 天」與「再前 7 天」，只報變慢最多的前 3 項；沒有就輸出 __SILENT__。

與 tool-reliability channel 的關係（使用者 2026-11-10 拍板另開 channel、不動 tool-reliability）：
那支 2026-08-01 決定「不收 latency」，理由一是並行工具共用時間戳會失真，二是沒門檻沒消費者就別收。
- 理由一：2026-11-10 實測最近 40 份主 session，多工具訊息 1,544 則時間戳各異、8 則相同，共用時間戳
  已不成立；但同批工具若依序執行，後面那個會吃到前面的執行時間，所以這裡只計「單獨發出」的呼叫。
- 理由二：消費者是 digest，訊號是週對週趨勢而不是絕對門檻；變慢條件只用來擋雜訊，避免每週硬湊 3 項。
機制只負責提醒；看到變慢後的深挖（量為什麼慢、最便宜修法、前後量測、證明結果不變）由使用者手動決定。

用法：
  python3 tool-latency-extract.py --report [--date YYYY-MM-DD]

--date 可回溯（例如拿舊日期驗證抓不抓得到已知的變慢）；檔案只用 mtime 擋掉不可能含視窗內事件的舊檔。
"""

import argparse
import datetime as dt
import json
import os
import re
import shlex
import statistics
import sys
from collections import defaultdict
from pathlib import Path

PROJECTS = Path(os.environ.get("TOOL_LATENCY_PROJECTS", str(Path.home() / ".claude" / "projects")))

WINDOW_DAYS = 7
CAP_SECONDS = 15 * 60
MIN_CALLS = 10
MIN_GROWTH = 0.20
MIN_EXTRA_SECONDS = 60
TOP_N = 3
TOP_TABLE = 10
# 耗時不是工具本身慢：等人回應的是人的反應時間，委派類是被委派工作的工作量
# （2026-11-10 回溯實測：沒排除時 Agent 中位數隨前景／背景派工比例從 0.2 秒跳到 33 秒，佔滿榜首）
EXCLUDED_TOOLS = {"AskUserQuestion", "ExitPlanMode", "Agent", "Task", "TaskOutput", "Workflow"}
INTERPRETERS = {"python", "python3", "bash", "sh", "zsh", "node", "bun", "deno", "ruby", "perl", "tsx", "osascript"}
PACKAGE_RUNNERS = {"pnpm", "npm", "yarn", "npx", "bunx", "uv", "uvx"}
SUBCOMMAND_TOOLS = {"git", "gh", "docker", "brew", "launchctl", "herdr", "hc", "ccsi", "semble"}
SKIP_PROGRAMS = {"cd", "set", "export", "true", ":", "pushd", "popd"}
# 常見的開場動作：後面還有別的程式就取後面那個，整串只有它們才用它們分組
PREFIX_PROGRAMS = {"echo", "printf", "mkdir", "source", "."}
SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||\n")
SHELL_KEYWORDS = {"for", "while", "until", "if", "case", "select", "do", "then", "else", "elif", "done", "fi", "esac", "{", "}"}
# 沒有固定身分的分組：每次內容不同的即興腳本、迴圈、agent 自選長短的 sleep；留在總表但不參加變慢排名
# （2026-11-10 回溯 4 個週二，這幾類佔滿前 3 名，把真正變慢的 closeout-provenance.py 擠出榜外）
NO_IDENTITY = {"(loop/control)", "sleep", "bash (empty)"}
MIN_DELTA_SECONDS = 0.5
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SCRIPT_EXT = re.compile(r"\.(?:py|sh|mjs|cjs|js|ts|rb|pl)$")


def parse_ts(ts):
  if not ts:
    return None
  try:
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
  except ValueError:
    return None


def strip_substitutions(command):
  # `S=$(date +%s)` 不先拿掉會被切成 `+%s)` 這種假分組；內層常有括號（python3 -c "...(...)"），所以要成對計數
  out, i, n = [], 0, len(command)
  while i < n:
    if command.startswith("$(", i):
      depth, j = 1, i + 2
      while j < n and depth:
        depth += {"(": 1, ")": -1}.get(command[j], 0)
        j += 1
      out.append("X")
      i = j
    elif command[i] == "`":
      j = command.find("`", i + 1)
      out.append("X")
      i = n if j < 0 else j + 1
    else:
      out.append(command[i])
      i += 1
  return "".join(out)


def _tokens(segment):
  try:
    return shlex.split(segment, comments=False, posix=True)
  except ValueError:
    return segment.split()


def _program_key(tokens):
  while tokens and (ASSIGNMENT.match(tokens[0]) or tokens[0] in ("env", "command", "time", "exec", "nohup")):
    tokens = tokens[1:]
  if not tokens:
    return None
  if tokens[0] in SHELL_KEYWORDS:
    return "(loop/control)"
  prog = os.path.basename(tokens[0])
  if prog in SKIP_PROGRAMS:
    return None
  if re.fullmatch(r"python3(?:\.\d+)?", prog):
    prog = "python3"
  if prog in INTERPRETERS:
    rest = tokens[1:]
    while rest and rest[0].startswith("-") and rest[0] not in ("-", "-m", "-c", "-e"):
      rest = rest[1:]
    if not rest or rest[0] in ("-", "-c", "-e") or rest[0].startswith("<<"):
      return f"{prog} (inline)"
    if rest[0] == "-m" and len(rest) > 1:
      return f"{prog} -m {rest[1]}"
    return os.path.basename(rest[0])
  if prog in PACKAGE_RUNNERS:
    rest = [t for t in tokens[1:] if not t.startswith("-")]
    if rest and rest[0] in ("run", "exec", "x"):
      rest = rest[1:]
    return f"{prog} {rest[0]}" if rest else prog
  if prog in SUBCOMMAND_TOOLS:
    rest = [t for t in tokens[1:] if not t.startswith("-")]
    if prog == "git" and len(tokens) > 2 and tokens[1] == "-C":
      rest = [t for t in tokens[3:] if not t.startswith("-")]
    return f"{prog} {rest[0]}" if rest else prog
  if "/" in tokens[0] or SCRIPT_EXT.search(prog):
    return prog
  return prog


def group_key(tool, tool_input):
  if tool != "Bash":
    return tool
  command = strip_substitutions(((tool_input or {}).get("command") or "").replace("\\\n", " "))
  prefix = None
  for segment in SEGMENT_SPLIT.split(command):
    segment = segment.strip().lstrip("(").rstrip(")").strip()
    if not segment:
      continue
    key = _program_key(_tokens(segment))
    if not key:
      continue
    if key in PREFIX_PROGRAMS:
      prefix = prefix or key
      continue
    return key
  return prefix or "bash (empty)"


def scan_file(path):
  uses = {}
  per_message = defaultdict(int)
  results = {}
  try:
    with open(path, encoding="utf-8", errors="replace") as handle:
      for index, line in enumerate(handle):
        if '"tool_use"' not in line and '"tool_result"' not in line:
          continue
        try:
          obj = json.loads(line)
        except json.JSONDecodeError:
          continue
        message = obj.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
          continue
        ts = obj.get("timestamp")
        # CC 把同一則訊息的每個 content block 各寫一行、共用 message.id；沒有 id 的退回以行為單位
        message_id = message.get("id") or f"line-{index}"
        for block in message["content"]:
          if not isinstance(block, dict):
            continue
          if block.get("type") == "tool_use" and block.get("id"):
            per_message[message_id] += 1
            uses.setdefault(block["id"], (ts, block.get("name"), block.get("input") or {}, message_id))
          elif block.get("type") == "tool_result" and block.get("tool_use_id"):
            results.setdefault(block["tool_use_id"], ts)
  except OSError:
    return []
  out = []
  for tool_id, (ts, name, tool_input, message_id) in uses.items():
    if per_message[message_id] != 1 or name in EXCLUDED_TOOLS:
      continue
    if name == "Bash" and isinstance(tool_input, dict) and tool_input.get("run_in_background"):
      continue
    start, end = parse_ts(ts), parse_ts(results.get(tool_id))
    if not start or not end:
      continue
    seconds = (end - start).total_seconds()
    if seconds < 0:
      continue
    out.append((tool_id, start, min(seconds, CAP_SECONDS), group_key(name, tool_input)))
  return out


def collect(projects, day):
  local_tz = dt.datetime.now().astimezone().tzinfo
  end = dt.datetime.combine(day, dt.time.min, tzinfo=local_tz)
  mid = end - dt.timedelta(days=WINDOW_DAYS)
  start = mid - dt.timedelta(days=WINDOW_DAYS)
  windows = {"cur": defaultdict(list), "prev": defaultdict(list)}
  seen = set()
  for path in projects.rglob("*.jsonl"):
    if "/subagents/" in str(path):
      continue
    try:
      if path.stat().st_mtime < start.timestamp():
        continue
    except OSError:
      continue
    for tool_id, began, seconds, key in scan_file(path):
      if tool_id in seen:
        continue
      seen.add(tool_id)
      if mid <= began < end:
        windows["cur"][key].append(seconds)
      elif start <= began < mid:
        windows["prev"][key].append(seconds)
  return windows, (start.date(), mid.date(), end.date())


def p90(values):
  ordered = sorted(values)
  return ordered[max(0, int(len(ordered) * 0.9) - 1)]


def slowdowns(windows):
  found = []
  for key, cur in windows["cur"].items():
    if key in NO_IDENTITY or key.endswith("(inline)"):
      continue
    prev = windows["prev"].get(key, [])
    if len(cur) < MIN_CALLS or len(prev) < MIN_CALLS:
      continue
    med_cur, med_prev = statistics.median(cur), statistics.median(prev)
    if med_prev <= 0 or med_cur < med_prev * (1 + MIN_GROWTH) or med_cur - med_prev < MIN_DELTA_SECONDS:
      continue
    extra = (med_cur - med_prev) * len(cur)
    if extra < MIN_EXTRA_SECONDS:
      continue
    found.append({"key": key, "extra": extra, "n": len(cur), "med_prev": med_prev, "med_cur": med_cur, "p90": p90(cur)})
  found.sort(key=lambda row: -row["extra"])
  return found[:TOP_N]


def fmt_seconds(value):
  return f"{value / 60:.1f} 分" if value >= 60 else f"{value:.1f} 秒"


def render(windows, bounds):
  rows = slowdowns(windows)
  if not rows:
    return "__SILENT__"
  start, mid, end = bounds
  last = end - dt.timedelta(days=1)
  lines = [f"## 工具耗時變慢：{mid}～{last}（對照 {start}～{mid - dt.timedelta(days=1)}）", ""]
  lines.append("**變慢最多的前 %d 項**（依「中位數增加 × 本週次數」估計多等的時間排序）" % len(rows))
  lines.append("")
  for row in rows:
    growth = row["med_cur"] / row["med_prev"] - 1
    lines.append(
      f"- `{row['key']}` 中位數 {fmt_seconds(row['med_prev'])} → {fmt_seconds(row['med_cur'])}（+{growth:.0%}），"
      f"本週 {row['n']} 次、p90 {fmt_seconds(row['p90'])}，估計本週多等 {fmt_seconds(row['extra'])}"
    )
  lines += [
    "",
    "要深挖就手動跑第 5 步：量為什麼慢、提最便宜的修法、量前後差異並證明結果不變。",
    "",
    f"<details><summary>本週總耗時前 {TOP_TABLE} 名</summary>",
    "",
    "| 分組 | 次數 | 總計 | 中位數 | p90 | 前週中位數 |",
    "|---|---|---|---|---|---|",
  ]
  ranked = sorted(windows["cur"].items(), key=lambda kv: -sum(kv[1]))[:TOP_TABLE]
  for key, values in ranked:
    prev = windows["prev"].get(key)
    prev_med = fmt_seconds(statistics.median(prev)) if prev else "—"
    lines.append(
      f"| {key} | {len(values)} | {fmt_seconds(sum(values))} | {fmt_seconds(statistics.median(values))} | {fmt_seconds(p90(values))} | {prev_med} |"
    )
  lines += [
    "",
    "</details>",
    "",
    f"（方法：主 session、同一則訊息只有一個工具呼叫才計；排除背景 Bash、等人回應與 Agent 等委派類工具；單次上限 15 分鐘。"
    f"變慢條件：兩週各 ≥{MIN_CALLS} 次、中位數 +{MIN_GROWTH:.0%} 且 +{MIN_DELTA_SECONDS} 秒以上、估計多等 ≥{MIN_EXTRA_SECONDS} 秒；"
    "即興腳本（inline）、迴圈與 sleep 只列總表、不參加排名。"
    "權限確認視窗的等待無法分離，會算進耗時。）",
  ]
  return "\n".join(lines)


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--report", action="store_true", required=True)
  parser.add_argument("--date", default=None, help="執行日（YYYY-MM-DD，預設今天）；比較的是這天之前的兩個 7 天")
  args = parser.parse_args()
  day = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
  windows, bounds = collect(PROJECTS, day)
  print(render(windows, bounds))
  return 0


if __name__ == "__main__":
  sys.exit(main())
