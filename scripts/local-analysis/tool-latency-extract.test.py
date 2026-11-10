#!/usr/bin/env python3
"""tool-latency-extract.py 的回歸 fixture。

跑法：python3 tool-latency-extract.test.py
鎖住的線：
  (1) 分組鍵取「實際跑的腳本或程式」，cd／變數賦值前綴不得變成分組
  (2) 只計單獨發出的呼叫：同一則訊息多個 tool_use、背景 Bash、等人回應與委派類工具都不計
  (3) 孤兒 tool_result 不計；單次上限 15 分鐘；同一個 tool_use id 跨檔只算一次；subagent 檔不掃
  (4) 變慢判定：兩週樣本都夠、中位數明顯上升才報；全部持平時輸出 __SILENT__（不硬湊前 3 名）
"""

import datetime as dt
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("tlx", Path(__file__).parent / "tool-latency-extract.py")
tlx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tlx)

PASS = FAIL = 0
DAY = dt.date(2026, 11, 10)
CUR_DAY = "2026-11-05"
PREV_DAY = "2026-10-29"


def check(name, want, got):
  global PASS, FAIL
  if want == got:
    print(f"  PASS  {name}")
    PASS += 1
  else:
    print(f"  FAIL  {name}\n        want {want!r}\n        got  {got!r}")
    FAIL += 1


def ts(day, seconds):
  base = dt.datetime.fromisoformat(f"{day}T12:00:00+00:00") + dt.timedelta(seconds=seconds)
  return base.strftime("%Y-%m-%dT%H:%M:%S.") + f"{base.microsecond // 1000:03d}Z"


def use_line(day, at, tool_id, name="Bash", tool_input=None, message_id=None, blocks=None):
  content = blocks or [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input or {}}]
  return {"type": "assistant", "timestamp": ts(day, at), "message": {"id": message_id or f"msg-{tool_id}", "content": content}}


def result_line(day, at, tool_id):
  return {"type": "user", "timestamp": ts(day, at), "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "x"}]}}


def call(day, at, tool_id, seconds, command):
  return [use_line(day, at, tool_id, tool_input={"command": command}), result_line(day, at + seconds, tool_id)]


def write(path, entries):
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


print("(1) 分組鍵")
check("python 腳本取檔名", "closeout-provenance.py", tlx.group_key("Bash", {"command": "python3 ~/.claude/scripts/closeout-provenance.py --repo ~/.claude --plan /tmp/x"}))
check("cd 與 -I 前綴略過", "closeout-provenance.py", tlx.group_key("Bash", {"command": "cd /tmp && python3 -I /Users/x/.claude/scripts/closeout-provenance.py --self-repos"}))
check("變數賦值段略過", "run-shell-channel.sh", tlx.group_key("Bash", {"command": "D=/tmp/a; bash /x/run-shell-channel.sh a b c false"}))
check("pnpm run 取 script 名", "pnpm typecheck", tlx.group_key("Bash", {"command": "pnpm run typecheck 2>&1 | tail -5"}))
check("git -C 取子命令", "git push", tlx.group_key("Bash", {"command": "git -C ~/.claude push origin main"}))
check("heredoc python 歸 inline", "python3 (inline)", tlx.group_key("Bash", {"command": "python3 -I - rows.json <<'EOF'\nprint(1)\nEOF"}))
check("一般程式取程式名", "sleep", tlx.group_key("Bash", {"command": "sleep 30"}))
check("echo 開場取後面的程式", "closeout-provenance.py", tlx.group_key("Bash", {"command": "echo '---'; python3 /x/closeout-provenance.py --self-repos"}))
check("整串只有 echo 才用 echo", "echo", tlx.group_key("Bash", {"command": "echo done"}))
check("$() 不產生假分組", "ccsi search", tlx.group_key("Bash", {"command": "S=$(date +%s); ccsi search foo -k 5"}))
check("$() 內層有括號也剝乾淨", "find", tlx.group_key("Bash", {"command": "P=$(python3 -c 'print(len(\"a\"))'); find ~/x -name y"}))
check("子 shell 括號剝掉", "bunx vitest", tlx.group_key("Bash", {"command": "(cd web && bunx vitest run --project dom)"}))
check("迴圈歸 (loop/control)", "(loop/control)", tlx.group_key("Bash", {"command": "for f in a b; do python3 /x/a.py $f; done"}))
check("source 當開場", "pytest", tlx.group_key("Bash", {"command": "source .venv/bin/activate && pytest -q"}))
check("換行續接不產生 \\ 分組", "curl", tlx.group_key("Bash", {"command": "curl -s https://x \\\n  -H 'a: b'"}))
check("非 Bash 用工具名", "WebFetch", tlx.group_key("WebFetch", {"url": "https://example.com"}))

print("(2)(3) 只計單獨發出的呼叫")
with tempfile.TemporaryDirectory() as tmp:
  projects = Path(tmp) / "projects"
  session = projects / "proj" / "s1.jsonl"
  batch = [
    use_line(CUR_DAY, 0, "b1", tool_input={"command": "sleep 1"}, message_id="batch"),
    use_line(CUR_DAY, 0, "b2", tool_input={"command": "sleep 2"}, message_id="batch"),
    result_line(CUR_DAY, 1, "b1"),
    result_line(CUR_DAY, 3, "b2"),
  ]
  entries = (
    call(CUR_DAY, 100, "solo", 4, "python3 /x/solo.py")
    + batch
    + [use_line(CUR_DAY, 200, "bg", tool_input={"command": "python3 /x/bg.py", "run_in_background": True}), result_line(CUR_DAY, 201, "bg")]
    + [use_line(CUR_DAY, 300, "ask", name="AskUserQuestion"), result_line(CUR_DAY, 900, "ask")]
    + [use_line(CUR_DAY, 350, "agent", name="Agent"), result_line(CUR_DAY, 380, "agent")]
    + [result_line(CUR_DAY, 400, "orphan")]
    + call(CUR_DAY, 500, "long", 3600, "python3 /x/long.py")
  )
  write(session, entries)
  got = {key: seconds for _id, _start, seconds, key in tlx.scan_file(session)}
  check("單獨呼叫有計時", 4.0, got.get("solo.py"))
  check("同一則訊息的兩個呼叫都不計", None, got.get("sleep"))
  check("背景 Bash 不計", None, got.get("bg.py"))
  check("等人回應的工具不計", None, got.get("AskUserQuestion"))
  check("委派類工具不計", None, got.get("Agent"))
  check("單次上限 15 分鐘", 900.0, got.get("long.py"))
  check("孤兒 tool_result 不產生紀錄", ["long.py", "solo.py"], sorted(got))

  write(projects / "proj" / "s1-copy.jsonl", call(CUR_DAY, 100, "solo", 4, "python3 /x/solo.py"))
  write(projects / "proj" / "s1" / "subagents" / "agent-a.jsonl", call(CUR_DAY, 700, "sub", 5, "python3 /x/sub.py"))
  windows, _bounds = tlx.collect(projects, DAY)
  check("同一個 tool_use id 跨檔只算一次", 1, len(windows["cur"]["solo.py"]))
  check("subagent 檔不掃", [], windows["cur"].get("sub.py", []))

print("(4) 變慢判定")
with tempfile.TemporaryDirectory() as tmp:
  projects = Path(tmp) / "projects"
  entries = []
  for i in range(12):
    entries += call(PREV_DAY, i * 100, f"slow-prev-{i}", 2, "python3 /x/slow.py")
    entries += call(CUR_DAY, i * 100, f"slow-cur-{i}", 8, "python3 /x/slow.py")
    entries += call(PREV_DAY, 5000 + i * 100, f"steady-prev-{i}", 3, "python3 /x/steady.py")
    entries += call(CUR_DAY, 5000 + i * 100, f"steady-cur-{i}", 3, "python3 /x/steady.py")
  for i in range(5):
    entries += call(PREV_DAY, 9000 + i * 100, f"rare-prev-{i}", 1, "python3 /x/rare.py")
    entries += call(CUR_DAY, 9000 + i * 100, f"rare-cur-{i}", 30, "python3 /x/rare.py")
  for i in range(12):
    entries += call(PREV_DAY, 20000 + i * 100, f"sleep-prev-{i}", 5, "sleep 5")
    entries += call(CUR_DAY, 20000 + i * 100, f"sleep-cur-{i}", 60, "sleep 60")
    entries += call(PREV_DAY, 30000 + i * 100, f"inline-prev-{i}", 1, "python3 - <<'EOF'\nprint(1)\nEOF")
    entries += call(CUR_DAY, 30000 + i * 100, f"inline-cur-{i}", 9, "python3 - <<'EOF'\nprint(2)\nEOF")
  # 中位數 +0.4 秒（+40%）但 300 次累積 2 分鐘：低於 0.5 秒下限，不算變慢
  for i in range(300):
    entries += call(PREV_DAY, 40000 + i * 10, f"tiny-prev-{i}", 1, "git commit -m x")
    entries += call(CUR_DAY, 40000 + i * 10, f"tiny-cur-{i}", 1.4, "git commit -m x")
  write(projects / "proj" / "s2.jsonl", entries)
  windows, bounds = tlx.collect(projects, DAY)
  rows = tlx.slowdowns(windows)
  check("只報樣本夠且明顯變慢的分組（sleep、inline、0.4 秒小變化都不進榜）", ["slow.py"], [r["key"] for r in rows])
  check("估計多等 =（中位數差）× 本週次數", 72.0, rows[0]["extra"] if rows else None)
  report = tlx.render(windows, bounds)
  check("報告列出變慢分組", True, "`slow.py` 中位數 2.0 秒 → 8.0 秒" in report)

with tempfile.TemporaryDirectory() as tmp:
  projects = Path(tmp) / "projects"
  entries = []
  for i in range(12):
    entries += call(PREV_DAY, i * 100, f"p-{i}", 3, "python3 /x/steady.py")
    entries += call(CUR_DAY, i * 100, f"c-{i}", 3, "python3 /x/steady.py")
  write(projects / "proj" / "s3.jsonl", entries)
  windows, bounds = tlx.collect(projects, DAY)
  check("全部持平時輸出 __SILENT__", "__SILENT__", tlx.render(windows, bounds))

print(f"\n{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
