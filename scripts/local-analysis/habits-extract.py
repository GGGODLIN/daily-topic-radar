#!/usr/bin/env python3
"""habits-extract.py — habits channel（E 線）的確定性前段。

從使用者自己打的訊息切出最近 21 天、每 7 天一段共 3 段，交給 local-analysis.js 的 3 個
miner agent 各讀一段。只做抽取與切段，不判斷哪些是「習慣」——那是 miner 與 merge 的事。

資料來源：`ccsi events --vendor cc --since <起日> --kind text`（逐行 JSON）。
收進來的條件（都是試做時實測出的噪音來源）：
- role=user；path 含 `/projects/-Users-linhancheng-`；排除 `/subagents/`
- 排除系統前綴（`<`、`[SYSTEM`、`Caveat` 等）、含 task-notification／system-reminder、長度 >2000
- 排除代理轉貼：開頭是 `[main]`、`[watcher]`、`[playtest-monitor]`、`Heads up`（試做時 miner
  回報這些不是使用者本人打的）
- 依 (session, ts, 前 80 字) 去重
每段檔頭附 ≤6 字短回覆次數前 30 名（「可以」「a」這類佔大宗但單看沒資訊），正文只放 >6 字的訊息。

輸出是使用者的私人訊息：目錄 0700、檔案 0600；預設落在 reports/local-analysis/habits/，
該路徑被 social-info 的 .gitignore（`reports/local-analysis/`）排除。--out-dir 改到別處時
不會替你檢查是否被 ignore。

用法：
  python3 -I habits-extract.py [--date YYYY-MM-DD] [--out-dir DIR]
stdout 是一份 JSON（date／since／slices[]），exit 0。
exit 2 = 參數錯誤或 ccsi 失敗；exit 3 = 三段合計 0 則訊息（多半是索引或過濾壞了，不當成「沒有習慣」）。
"""

import argparse
import collections
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
DEFAULT_OUT_DIR = HOME / "code" / "social-info" / "reports" / "local-analysis" / "habits"
WINDOW_DAYS = 21
SEGMENT_DAYS = 7
SHORT_MAX_CHARS = 6
MAX_MESSAGE_CHARS = 2000
TOP_SHORT = 30
PROJECT_MARK = "/projects/-Users-linhancheng-"

SKIP_PREFIXES = (
    "<",
    "[SYSTEM",
    "Caveat",
    "[Request interrupted",
    "Base directory for this skill",
    "Stop hook feedback",
    "This session is being continued",
    "(Re-invocation",
    "[main]",
    "[watcher]",
    "[playtest-monitor]",
    "Heads up",
)


def parse_user_message(event):
    """回傳 (ts, session, project, text)；不是使用者本人打的訊息回 None。"""
    if event.get("role") != "user":
        return None
    path = event.get("path") or ""
    if "/subagents/" in path or PROJECT_MARK not in path:
        return None
    text = (event.get("text") or "").strip()
    if not text or text.startswith(SKIP_PREFIXES) or len(text) > MAX_MESSAGE_CHARS:
        return None
    if "task-notification" in text or "system-reminder" in text:
        return None
    project = re.sub(r".*/projects/-Users-linhancheng-", "", path).split("/")[0]
    return (event.get("ts") or "", event.get("session") or "", project, text)


def collect_messages(events):
    seen = set()
    out = []
    for event in events:
        parsed = parse_user_message(event)
        if parsed is None:
            continue
        ts, session, _project, text = parsed
        key = (session, ts, text[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append(parsed)
    out.sort()
    return out


def segment_bounds(date):
    """回傳三段 (start, end)，皆含頭尾；最後一段的 end 是 date 當天。"""
    end = dt.date.fromisoformat(date)
    first = end - dt.timedelta(days=WINDOW_DAYS - 1)
    bounds = []
    for i in range(WINDOW_DAYS // SEGMENT_DAYS):
        start = first + dt.timedelta(days=i * SEGMENT_DAYS)
        bounds.append((start.isoformat(), (start + dt.timedelta(days=SEGMENT_DAYS - 1)).isoformat()))
    return bounds


def in_segment(message, start, end):
    return start <= message[0][:10] <= end


def render_slice(index, start, end, messages):
    short = collections.Counter(m[3] for m in messages if len(m[3]) <= SHORT_MAX_CHARS)
    body = [m for m in messages if len(m[3]) > SHORT_MAX_CHARS]
    sessions = {m[1] for m in messages}
    top = "；".join(f"{text}×{n}" for text, n in short.most_common(TOP_SHORT))
    lines = [
        f"# 第 {index} 段：{start} ~ {end}，共 {len(messages)} 則使用者訊息、{len(sessions)} 個 session，"
        f"其中 >{SHORT_MAX_CHARS} 字的 {len(body)} 則收在正文",
        f"## 短回覆次數（≤{SHORT_MAX_CHARS} 字，前 {TOP_SHORT}）",
        top,
        "",
        "## 訊息全文（時間｜session｜專案｜內容）",
    ]
    for ts, session, project, text in body:
        lines.append(f"{ts[:16]} | {session[:8]} | {project} | {text.replace(chr(10), ' ⏎ ')}")
    return "\n".join(lines) + "\n", len(body), len(sessions)


def write_private(path, text):
    # O_NOFOLLOW：目標若被換成 symlink 就拒寫，不把私人訊息寫到別處。
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(text)


def ccsi_events(since):
    binary = shutil.which("ccsi") or str(HOME / ".local" / "bin" / "ccsi")
    proc = subprocess.Popen(
        [binary, "events", "--vendor", "cc", "--since", since, "--kind", "text"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    for line in proc.stdout:
        try:
            yield json.loads(line)
        except ValueError:
            continue
    stderr = proc.stderr.read()
    if proc.wait() != 0:
        raise RuntimeError(f"ccsi events exit {proc.returncode}: {stderr.strip()[:300]}")


def file_events(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=dt.date.today().isoformat())
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    # 測試用：讀 ndjson 檔取代 ccsi，讓切段與過濾能用固定資料驗。
    parser.add_argument("--events-file", default=None)
    args = parser.parse_args(argv)

    try:
        bounds = segment_bounds(args.date)
    except ValueError:
        print(f"--date 必須是 YYYY-MM-DD：{args.date}", file=sys.stderr)
        return 2

    try:
        events = file_events(args.events_file) if args.events_file else ccsi_events(bounds[0][0])
        messages = collect_messages(events)
    except (OSError, RuntimeError) as err:
        print(f"抽取失敗：{err}", file=sys.stderr)
        return 2

    per_slice = [[m for m in messages if in_segment(m, s, e)] for s, e in bounds]
    if not any(per_slice):
        print("三段合計 0 則訊息：疑似 ccsi 索引或過濾條件壞了，不寫檔", file=sys.stderr)
        return 3

    out_dir = Path(args.out_dir)
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)

    slices = []
    for i, ((start, end), msgs) in enumerate(zip(bounds, per_slice), start=1):
        text, body_count, session_count = render_slice(i, start, end, msgs)
        path = out_dir / f"{args.date}-habits-slice-{i}.txt"
        write_private(path, text)
        slices.append({
            "index": i,
            "start": start,
            "end": end,
            "path": str(path),
            "total_messages": len(msgs),
            "body_messages": body_count,
            "sessions": session_count,
        })
    print(json.dumps({"date": args.date, "since": bounds[0][0], "slices": slices}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
