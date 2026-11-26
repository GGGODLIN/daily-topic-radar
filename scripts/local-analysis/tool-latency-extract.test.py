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


def safe_call(fn, *args, **kwargs):
  try:
    return fn(*args, **kwargs)
  except Exception as exc:
    return ("EXC", type(exc).__name__)


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
check("python 腳本取檔名與模式旗標", "closeout-provenance.py --plan", tlx.group_key("Bash", {"command": "python3 ~/.claude/scripts/closeout-provenance.py --repo ~/.claude --plan /tmp/x"}))
check("cd 與 -I 前綴略過並保留模式旗標", "closeout-provenance.py --self-repos", tlx.group_key("Bash", {"command": "cd /tmp && python3 -I /Users/x/.claude/scripts/closeout-provenance.py --self-repos"}))
check("無模式旗標的腳本只取檔名", "solo.py", tlx.group_key("Bash", {"command": "python3 /x/solo.py --repo /tmp/r"}))
check("腳本明確子命令納入分組", "memory-origin.py scan", tlx.group_key("Bash", {"command": "python3 /x/memory-origin.py scan --cap 30"}))
check("變數賦值段略過", "run-shell-channel.sh", tlx.group_key("Bash", {"command": "D=/tmp/a; bash /x/run-shell-channel.sh a b c false"}))
check("pnpm run 取 script 名", "pnpm typecheck", tlx.group_key("Bash", {"command": "pnpm run typecheck 2>&1 | tail -5"}))
check("git -C 取子命令", "git push", tlx.group_key("Bash", {"command": "git -C ~/.claude push origin main"}))
check("heredoc python 歸 inline", "python3 (inline)", tlx.group_key("Bash", {"command": "python3 -I - rows.json <<'EOF'\nprint(1)\nEOF"}))
check("一般程式取程式名", "sleep", tlx.group_key("Bash", {"command": "sleep 30"}))
check("echo 開場取後面的程式", "closeout-provenance.py --self-repos", tlx.group_key("Bash", {"command": "echo '---'; python3 /x/closeout-provenance.py --self-repos"}))
check("scp && ssh 多工具串接歸 (compound)", "(compound)", tlx.group_key("Bash", {"command": "scp -q a calyx-m4:/tmp/a && ssh calyx-m4 'pytest -q'"}))
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
    + [use_line(CUR_DAY, 420, "err1", tool_input={"command": "python3 /x/err.py"}), {"type": "user", "timestamp": ts(CUR_DAY, 421), "message": {"content": [{"type": "tool_result", "tool_use_id": "err1", "is_error": True, "content": "boom"}]}}]
    + [use_line(CUR_DAY, 430, "skip1", tool_input={"command": "python3 /x/skip.py"}), {"type": "user", "timestamp": ts(CUR_DAY, 430.2), "message": {"content": [{"type": "tool_result", "tool_use_id": "skip1", "content": '{"status": "skip", "reason": "empty-diff"}'}]}}]
    + call(CUR_DAY, 500, "long", 3600, "python3 /x/long.py")
  )
  write(session, entries)
  got = {key: seconds for _id, _start, seconds, key in tlx.scan_file(session)}
  check("單獨呼叫有計時", 4.0, got.get("solo.py"))
  check("同一則訊息的兩個呼叫都不計", None, got.get("sleep"))
  check("背景 Bash 不計", None, got.get("bg.py"))
  check("等人回應的工具不計", None, got.get("AskUserQuestion"))
  check("委派類工具不計", None, got.get("Agent"))
  check("is_error 失敗呼叫不計入基線", None, got.get("err.py"))
  check("structured skip 快退不計入基線", None, got.get("skip.py"))
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

print("(5) 回歸防護 T1–T13")
# T1: parse_ts 非字串 / naive / 跨時區
check("T1 parse_ts 非字串回 None", None, safe_call(tlx.parse_ts, 12345))
check("T1 parse_ts naive 時間戳回 None", None, safe_call(tlx.parse_ts, "2026-11-05T12:00:00"))
with tempfile.TemporaryDirectory() as tmp:
  projects = Path(tmp) / "projects"
  s_mix = projects / "proj" / "mix.jsonl"
  mixed_entries = [
    {"type": "assistant", "timestamp": "2026-11-05T12:00:00", "message": {"id": "m-mix", "content": [{"type": "tool_use", "id": "mix1", "name": "Bash", "input": {"command": "python3 /x/mix.py"}}]}},
    {"type": "user", "timestamp": "2026-11-05T12:00:05Z", "message": {"content": [{"type": "tool_result", "tool_use_id": "mix1", "content": "ok"}]}},
    {"type": "assistant", "timestamp": "2026-11-05T20:00:00+08:00", "message": {"id": "m-tz", "content": [{"type": "tool_use", "id": "tz1", "name": "Bash", "input": {"command": "python3 /x/tz.py"}}]}},
    {"type": "user", "timestamp": "2026-11-05T12:00:05.000Z", "message": {"content": [{"type": "tool_result", "tool_use_id": "tz1", "content": "ok"}]}},
  ]
  write(s_mix, mixed_entries)
  check(
    "T1 混合 naive/aware 不潰並精確計算跨時區秒數",
    {"tz.py": 5.0},
    safe_call(lambda p: {k: s for _id, _st, s, k in tlx.scan_file(p)}, s_mix),
  )

  s_naive = projects / "proj" / "naive.jsonl"
  naive_entries = [
    {"type": "assistant", "timestamp": "2026-11-05T12:00:00", "message": {"id": "m-n1", "content": [{"type": "tool_use", "id": "n1", "name": "Bash", "input": {"command": "python3 /x/naive.py"}}]}},
    {"type": "user", "timestamp": "2026-11-05T12:00:05", "message": {"content": [{"type": "tool_result", "tool_use_id": "n1", "content": "ok"}]}},
  ] + call(CUR_DAY, 10, "aw1", 4, "python3 /x/aware.py")
  write(s_naive, naive_entries)
  s_mix.unlink()
  check(
    "T1 collect 遇 naive 時間戳不 TypeError 且保留有效資料",
    {"aware.py": [4.0]},
    safe_call(lambda d: dict(tlx.collect(d, DAY)[0]["cur"]), projects),
  )

# T2: 同一個 message.id 重複相同 tool_use id（streaming/resume 產物）仍算 solo
with tempfile.TemporaryDirectory() as tmp:
  s_dup = Path(tmp) / "dup.jsonl"
  dup_entries = [
    use_line(CUR_DAY, 10, "dup1", tool_input={"command": "python3 /x/dup.py"}, message_id="msg-dup"),
    use_line(CUR_DAY, 10, "dup1", tool_input={"command": "python3 /x/dup.py"}, message_id="msg-dup"),
    result_line(CUR_DAY, 14, "dup1"),
  ]
  write(s_dup, dup_entries)
  got_dup = safe_call(lambda p: {k: s for _id, _st, s, k in tlx.scan_file(p)}, s_dup)
  check("T2 同 message.id 重複同一個 tool_use id 仍計為 solo", 4.0, got_dup.get("dup.py") if isinstance(got_dup, dict) else got_dup)

# T3: 畸形 JSONL 列與無效識別欄位不崩潰
with tempfile.TemporaryDirectory() as tmp:
  p_raw = Path(tmp) / "raw_bad.jsonl"
  p_raw.write_text(
    "\n".join([
      "null",
      '["tool_use"]',
      "123",
      json.dumps(call(CUR_DAY, 10, "ok1", 4, "python3 /x/valid.py")[0]),
      json.dumps(call(CUR_DAY, 10, "ok1", 4, "python3 /x/valid.py")[1]),
    ]) + "\n"
  )
  check("T3 非 dict JSONL 列（null/[]/123）不潰且保留有效呼叫", {"valid.py": 4.0}, safe_call(lambda p: {k: s for _id, _st, s, k in tlx.scan_file(p)}, p_raw))

  p_mid = Path(tmp) / "bad_mid.jsonl"
  bad_mid = [
    {"type": "assistant", "timestamp": ts(CUR_DAY, 0), "message": {"id": {"k": 1}, "content": [{"type": "tool_use", "id": "bm1", "name": "Bash", "input": {"command": "python3 /x/bad.py"}}]}},
    result_line(CUR_DAY, 2, "bm1"),
  ] + call(CUR_DAY, 10, "ok2", 4, "python3 /x/valid.py")
  write(p_mid, bad_mid)
  check("T3 unhashable message.id 不潰且保留有效呼叫", {"valid.py": 4.0}, safe_call(lambda p: {k: s for _id, _st, s, k in tlx.scan_file(p)}, p_mid))

  p_tid = Path(tmp) / "bad_tid.jsonl"
  bad_tid = [
    {"type": "assistant", "timestamp": ts(CUR_DAY, 0), "message": {"id": "m-bt1", "content": [{"type": "tool_use", "id": ["x"], "name": "Bash", "input": {"command": "python3 /x/bad.py"}}]}},
  ] + call(CUR_DAY, 10, "ok3", 4, "python3 /x/valid.py")
  write(p_tid, bad_tid)
  check("T3 unhashable tool_use.id 不潰且保留有效呼叫", {"valid.py": 4.0}, safe_call(lambda p: {k: s for _id, _st, s, k in tlx.scan_file(p)}, p_tid))

  p_rtid = Path(tmp) / "bad_rtid.jsonl"
  bad_rtid = [
    {"type": "user", "timestamp": ts(CUR_DAY, 5), "message": {"content": [{"type": "tool_result", "tool_use_id": {"x": 1}, "content": "x"}]}},
  ] + call(CUR_DAY, 10, "ok4", 4, "python3 /x/valid.py")
  write(p_rtid, bad_rtid)
  check("T3 unhashable tool_result.tool_use_id 不潰且保留有效呼叫", {"valid.py": 4.0}, safe_call(lambda p: {k: s for _id, _st, s, k in tlx.scan_file(p)}, p_rtid))

  projects = Path(tmp) / "projects"
  name_entries = []
  for i in range(12):
    name_entries += [use_line(PREV_DAY, i * 10, f"np-{i}", name=None), result_line(PREV_DAY, i * 10 + 1, f"np-{i}")]
    name_entries += [use_line(CUR_DAY, i * 10, f"nc-{i}", name=None), result_line(CUR_DAY, i * 10 + 5, f"nc-{i}")]
    name_entries += [use_line(PREV_DAY, 200 + i * 10, f"ip-{i}", name=123), result_line(PREV_DAY, 200 + i * 10 + 1, f"ip-{i}")]
    name_entries += [use_line(CUR_DAY, 200 + i * 10, f"ic-{i}", name=123), result_line(CUR_DAY, 200 + i * 10 + 5, f"ic-{i}")]
    name_entries += call(PREV_DAY, 500 + i * 100, f"sp-{i}", 2, "python3 /x/slow.py")
    name_entries += call(CUR_DAY, 500 + i * 100, f"sc-{i}", 8, "python3 /x/slow.py")
  write(projects / "proj" / "bad_names.jsonl", name_entries)
  check(
    "T3 非字串 tool_use.name（None / 123）不讓 slowdowns 崩潰",
    ["slow.py"],
    safe_call(lambda d: [r["key"] for r in tlx.slowdowns(tlx.collect(d, DAY)[0])], projects),
  )

# T4: group_key 非 dict tool_input / 非字串 command
check("T4 group_key 非 dict tool_input 回 bash (empty)", "bash (empty)", safe_call(tlx.group_key, "Bash", "not-a-dict"))
check("T4 group_key 非字串 command 回 bash (empty)", "bash (empty)", safe_call(tlx.group_key, "Bash", {"command": 123}))

# T5: here-string <<< 與引號內 <<EOF 不誤判成 heredoc
check("T5 here-string <<< 不誤刪後續行", "(compound)", safe_call(tlx.group_key, "Bash", {"command": "cat <<< EOF\npython3 /x/foo.py"}))
check("T5 引號內 <<EOF 不誤刪後續行", "foo.py", safe_call(tlx.group_key, "Bash", {"command": "echo '<<EOF'\npython3 /x/foo.py"}))

# T6: 引號內 && ; | 不被當成命令分隔符
check("T6 引號內 && ; | 不誤切成 (compound)", "git commit", safe_call(tlx.group_key, "Bash", {"command": 'git commit -m "fix: a && b; c | d"'}))

# T7: 直譯器旗標解析（bash -e / 合併短旗標 -lc / -Ic / -Im）
check("T7 bash -e 取實際腳本而非 inline", "deploy.sh", safe_call(tlx.group_key, "Bash", {"command": "bash -e /x/deploy.sh"}))
check("T7 bash -lc 識別為 bash (inline)", "bash (inline)", safe_call(tlx.group_key, "Bash", {"command": "bash -lc 'echo hi'"}))
check("T7 python3 -Ic 識別為 python3 (inline)", "python3 (inline)", safe_call(tlx.group_key, "Bash", {"command": "python3 -Ic 'print(1)'"}))
check("T7 python3 -Im 取模組名", "python3 -m pytest", safe_call(tlx.group_key, "Bash", {"command": "python3 -Im pytest"}))

# T8: shell 註解行與行尾註解不產生假步驟
check("T8 整行註解與行尾註解不產生 (compound)", "foo.py", safe_call(tlx.group_key, "Bash", {"command": "# comment line\npython3 /x/foo.py # trailing comment"}))

# T9: tool_result content 為多 block 清單時須掃全 block 判 skip
with tempfile.TemporaryDirectory() as tmp:
  s_skip = Path(tmp) / "multiblock_skip.jsonl"
  skip_entries = [
    use_line(CUR_DAY, 10, "sk1", tool_input={"command": "python3 /x/silent2.py"}),
    {"type": "user", "timestamp": ts(CUR_DAY, 11), "message": {"content": [{"type": "tool_result", "tool_use_id": "sk1", "content": [{"type": "text", "text": "info"}, {"type": "text", "text": "__SILENT__"}]}]}},
    use_line(CUR_DAY, 20, "sk2", tool_input={"command": "python3 /x/skip2.py"}),
    {"type": "user", "timestamp": ts(CUR_DAY, 21), "message": {"content": [{"type": "tool_result", "tool_use_id": "sk2", "content": [{"type": "text", "text": "info"}, {"type": "text", "text": '{"status": "skip"}'}]}]}},
  ]
  write(s_skip, skip_entries)
  got_skip = safe_call(lambda p: [k for _id, _st, _s, k in tlx.scan_file(p)], s_skip)
  check("T9 多 block tool_result 含 __SILENT__ 或 status:skip 皆排除", [], got_skip)

# T10: p90 空清單與小樣本 nearest-rank
check("T10 p90 空清單回 0.0", 0.0, safe_call(tlx.p90, []))
check("T10 p90([1, 2, 3]) 用 nearest-rank 回 3", 3, safe_call(tlx.p90, [1, 2, 3]))

# T11: 邊界浮點誤差（0.7 - 0.2 = 0.49999999999999994 且 × 120 = 59.99999999999999）
with tempfile.TemporaryDirectory() as tmp:
  projects = Path(tmp) / "projects"
  fp_entries = []
  for i in range(120):
    fp_entries += call(PREV_DAY, i * 10, f"fpp-{i}", 0.2, "python3 /x/fp.py")
    fp_entries += call(CUR_DAY, i * 10, f"fpc-{i}", 0.7, "python3 /x/fp.py")
  write(projects / "proj" / "fp.jsonl", fp_entries)
  fp_win, fp_bounds = tlx.collect(projects, DAY)
  check("T11 精確 +0.5s × 120 = 60.0s 邊界浮點誤差不漏報", ["fp.py"], [r["key"] for r in tlx.slowdowns(fp_win)])
  check("T11 render 不回 __SILENT__", True, tlx.render(fp_win, fp_bounds) != "__SILENT__")

# T12: symlinked 專案目錄須掃描；父層含 subagents 不誤殺，真正 subagents 仍排除
with tempfile.TemporaryDirectory() as tmp:
  real_proj = Path(tmp) / "real_proj"
  write(real_proj / "s_sym.jsonl", call(CUR_DAY, 10, "sym1", 4, "python3 /x/sym.py"))
  sym_projects = Path(tmp) / "projects_sym"
  sym_projects.mkdir()
  (sym_projects / "linked-proj").symlink_to(real_proj)
  check("T12a symlinked 專案目錄有掃到", {"sym.py": [4.0]}, safe_call(lambda d: dict(tlx.collect(d, DAY)[0]["cur"]), sym_projects))

  sub_parent_projects = Path(tmp) / "subagents" / "projects"
  write(sub_parent_projects / "proj" / "main.jsonl", call(CUR_DAY, 10, "mp1", 4, "python3 /x/main.py"))
  write(sub_parent_projects / "proj" / "s1" / "subagents" / "agent.jsonl", call(CUR_DAY, 20, "sa1", 4, "python3 /x/sub.py"))
  check(
    "T12b projects 父層名為 subagents 不誤殺主線，仍排除底層 subagents",
    {"main.py": [4.0]},
    safe_call(lambda d: dict(tlx.collect(d, DAY)[0]["cur"]), sub_parent_projects),
  )

# T13: 同 extra / 同總耗時平手時依 key 名稱決定性排序
tie_windows = {
  "cur": {"zebra.py": [8.0] * 12, "alpha.py": [8.0] * 12},
  "prev": {"zebra.py": [1.0] * 12, "alpha.py": [1.0] * 12},
}
tie_bounds = (dt.date(2026, 10, 27), dt.date(2026, 11, 3), DAY)
check("T13 slowdowns 平手依 key 決定性排序", ["alpha.py", "zebra.py"], [r["key"] for r in tlx.slowdowns(tie_windows)])
tie_report = tlx.render(tie_windows, tie_bounds)
check(
  "T13 render 總表平手依 key 決定性排序",
  True,
  tie_report.index("| alpha.py |") < tie_report.index("| zebra.py |"),
)

# Sol T01: 較早無效回覆不遮蔽後續合法回覆
with tempfile.TemporaryDirectory() as td:
  p_t01 = Path(td) / "t01.jsonl"
  rows_t01 = [
    {"timestamp": "2026-11-08T10:00:05Z", "message": {"id": "m1", "content": [{"type": "tool_use", "id": "u1", "name": "Read", "input": {}}]}},
    {"timestamp": "2026-11-08T10:00:01Z", "message": {"id": "r0", "content": [{"type": "tool_result", "tool_use_id": "u1", "content": "early-stale"}]}},
    {"timestamp": "2026-11-08T10:00:09Z", "message": {"id": "r1", "content": [{"type": "tool_result", "tool_use_id": "u1", "content": "ok"}]}},
  ]
  p_t01.write_text("\n".join(json.dumps(r) for r in rows_t01) + "\n", encoding="utf-8")
  out_t01 = tlx.scan_file(p_t01)
  check("Sol T01 忽略早於 tool_use 的無效回覆、取最早合法回覆", [4.0], [r[2] for r in out_t01])

# Sol T06: 有變慢項時空桶不讓 render 崩潰
empty_bucket_windows = {
  "cur": {"slow.py": [8.0] * 12, "empty.py": []},
  "prev": {"slow.py": [1.0] * 12, "empty.py": [1.0] * 12},
}
empty_report = tlx.render(empty_bucket_windows, tie_bounds)
check("Sol T06 有變慢項時空桶不破壞 render", True, "slow.py" in empty_report and "| empty.py |" not in empty_report)

# Wave 35 回歸防護：壞格式 JSON、裸 CR、Unicode 空白與 collect/scan_file 窗內惰性分組等價
with tempfile.TemporaryDirectory() as td:
  tdp = Path(td)
  # 1. 6KB tool_result 尾端含非法跳脫 \q 必整行拒收
  f_bad = tdp / "bad_escape.jsonl"
  f_bad.write_text(
    '{"timestamp":"2026-11-08T10:00:00Z","message":{"id":"m1","content":[{"type":"tool_use","id":"t1","name":"Read","input":{}}]}}\n'
    + '{"timestamp":"2026-11-08T10:00:05Z","message":{"content":[{"type":"tool_result","tool_use_id":"t1","content":"' + ("a" * 6000) + r'\q"}]}}' + "\n",
    encoding="utf-8",
  )
  check("W35 6KB tool_result 含非法跳脫拒收", [], tlx.scan_file(f_bad))

  # 2. 兩行壞 JSON 不可跨行拼成合法紀錄
  f_splice = tdp / "splice.jsonl"
  f_splice.write_text(
    '{"a":"tool_use","b":[1\n'
    '2]},{"timestamp":"2026-11-08T10:00:00Z","message":{"id":"m1","content":[{"type":"tool_use","id":"t1","name":"Read","input":{}}]}}\n'
    '{"timestamp":"2026-11-08T10:00:05Z","message":{"content":[{"type":"tool_result","tool_use_id":"t1","content":"ok"}]}}\n',
    encoding="utf-8",
  )
  check("W35 兩行壞 JSON 不跨行錯位拼裝", [], tlx.scan_file(f_splice))

  # 3. 裸 \r 換行仍照通用換行解析
  f_cr = tdp / "bare_cr.jsonl"
  f_cr.write_bytes((
    '{"timestamp":"2026-11-08T10:00:00Z","message":{"id":"m1","content":[{"type":"tool_use","id":"t1","name":"Read","input":{}}]}}\r'
    '{"timestamp":"2026-11-08T10:00:05Z","message":{"content":[{"type":"tool_result","tool_use_id":"t1","content":"ok"}]}}\r'
  ).encode("utf-8"))
  check("W35 裸 CR 換行維持等價解析", [5.0], [r[2] for r in tlx.scan_file(f_cr)])

  # 4. Unicode 空白 (\xa0 / 　) 與未閉合引號在 _tokens 與 shlex 一致
  check("W35 _tokens 保留 NBSP 不誤切", ["python3", "foo\xa0bar.py"], tlx._tokens("python3 foo\xa0bar.py"))
  check("W35 _tokens 保留全形空白不誤切", ["python3", "foo　bar.py"], tlx._tokens("python3 foo　bar.py"))
  check("W35 未閉合引號安全退回", ["echo", "'unclosed"], tlx._tokens("echo 'unclosed"))

  # 5. collect() 與 scan_file() 分桶等價（含 mtime 新但窗外事件＋跨檔重複 tid）
  proj = tdp / "proj"
  old_ev = call(dt.date(2026, 10, 1), 10, "old1", 3, "python3 /x/old.py")
  cur_ev = call(CUR_DAY, 10, "dup1", 6, "python3 /x/cur.py --fast")
  prev_ev = call(PREV_DAY, 10, "prev1", 2, "python3 /x/cur.py --fast")
  write(proj / "a" / "s1.jsonl", old_ev + cur_ev + prev_ev)
  write(proj / "b" / "s2.jsonl", cur_ev)
  win_c, bounds_c = tlx.collect(proj, DAY)
  start_dt, mid_dt, end_dt = (dt.datetime.combine(d, dt.time.min).astimezone(dt.timezone.utc) for d in bounds_c)
  expected_cur, expected_prev, seen_t = {}, {}, set()
  for fp in sorted(proj.rglob("*.jsonl")):
    for tid, began, secs, key in tlx.scan_file(fp):
      if tid in seen_t:
        continue
      seen_t.add(tid)
      if mid_dt <= began < end_dt:
        expected_cur.setdefault(key, []).append(secs)
      elif start_dt <= began < mid_dt:
        expected_prev.setdefault(key, []).append(secs)
  check("W35 collect 與 scan_file 分桶等價(cur)", expected_cur, dict(win_c["cur"]))
  check("W35 collect 與 scan_file 分桶等價(prev)", expected_prev, dict(win_c["prev"]))

print(f"\n{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
