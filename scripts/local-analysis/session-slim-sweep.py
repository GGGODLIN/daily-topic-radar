#!/usr/bin/env python3
"""Run session-slim on ended Claude Code sessions, and compress cold plain-text CC and ended pi sessions.

Threat model: 這是給誠實系統用的自動整理，不是安全閘門；判斷不出 session 是否結束時一律不動，留給下一輪。
不保證：只認得程序名稱是 claude 的 Claude Code（用 node 跑的不算）；註冊檔不在 ~/.claude/sessions 的
claude 程序（例如另一個 CLAUDE_CONFIG_DIR）會讓整輪不動；pi 沒有註冊檔，只靠「沒被任何程序開著＋閒置」判斷；
程式被強制結束時 SessionEnd 可能沒觸發，由每小時補漏接手。
停用：建立 <state>/disabled。
"""
import argparse
import importlib.util
import json
import mmap
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()
STATE = Path(os.environ.get("SESSION_SLIM_STATE", HOME / ".local/state/session-slim"))
CC_ROOT = Path(os.environ.get("SESSION_SLIM_CC_ROOT", HOME / ".claude/projects"))
PI_ROOT = Path(os.environ.get("SESSION_SLIM_PI_ROOT", HOME / ".pi/agent/sessions"))
AUDIT_STATE = Path(os.environ.get("SESSION_SLIM_AUDIT_STATE", HERE.parents[1] / "reports/local-analysis/session-audit"))
REGISTRY = Path(os.environ.get("SESSION_SLIM_REGISTRY", HOME / ".claude/sessions"))
PS_LINE = re.compile(r"\s*(?P<pid>\d+)\s+(?P<start>\w{3} \w{3} +\d+ \d\d:\d\d:\d\d \d{4})\s+(?P<comm>.+)$")
IDLE_SECONDS = 30 * 60
QUEUE_SETTLE_SECONDS = 60
QUEUE_WAIT_SECONDS = 120
SWEEP_DAYS = 14
COLD_DAYS = 30
MIN_BYTES = 256 * 1024


def load_slim():
  spec = importlib.util.spec_from_file_location("session_slim", HERE / "session-slim.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def append_line(path, record):
  # 只追加一般檔：佇列或紀錄被換成 symlink／FIFO 時直接放棄，不寫到別處、不卡住
  path.parent.mkdir(parents=True, exist_ok=True)
  fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
  try:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
      return False
    os.write(fd, (json.dumps(record, ensure_ascii=False) + "\n").encode())
    return True
  finally:
    os.close(fd)


def enqueue(stdin_text, state=STATE):
  try:
    payload = json.loads(stdin_text or "{}")
  except ValueError:
    return False
  path = payload.get("transcript_path")
  if not isinstance(path, str) or not path.endswith(".jsonl"):
    return False
  try:
    resolved = Path(path).resolve()
    resolved.relative_to(CC_ROOT.resolve())
  except (OSError, ValueError):
    return False
  return append_line(state / "queue.jsonl", {"path": str(resolved), "session_id": payload.get("session_id", ""), "at": time.time()})


class LiveUnknown(Exception):
  pass


def live_sessions():
  # 不問 Herdr：Herdr 沒在跑或換版時 agent list 拿不到，整個排程就停擺，而且不在 Herdr 裡的 session 根本不在名單上。
  # Claude Code 自己寫的 ~/.claude/sessions/<pid>.json 不依賴任何終端機；任何一個 claude 程序對不上註冊檔就丟
  # LiveUnknown，呼叫端整輪不動。
  try:
    proc = subprocess.run(["ps", "-axo", "pid=,lstart=,comm="], capture_output=True, text=True, timeout=20,
                          env=dict(os.environ, TZ="UTC", LC_ALL="C"), check=True)
  except (OSError, subprocess.SubprocessError) as error:
    raise LiveUnknown(f"ps failed: {error}") from error
  live = set()
  for line in proc.stdout.splitlines():
    match = PS_LINE.match(line)
    if not match or Path(match["comm"].strip()).name != "claude":
      continue
    pid = int(match["pid"])
    try:
      record = json.loads((REGISTRY / f"{pid}.json").read_text())
    except (OSError, ValueError) as error:
      raise LiveUnknown(f"claude pid {pid} has no readable registry") from error
    # 比對啟動時間：pid 被重用時，舊程序留下的註冊檔不能拿來代表新程序
    started = " ".join(str(record.get("procStart", "")).split())
    if record.get("pid") != pid or started != " ".join(match["start"].split()) or not record.get("sessionId"):
      raise LiveUnknown(f"claude pid {pid} registry does not match the process")
    live.add(str(record["sessionId"]))
  return live


def opened(path):
  # pi 沒有註冊檔，被任何程序開著就當還在用；lsof 出錯或跑不了一律當開著
  try:
    proc = subprocess.run(["lsof", "-t", "--", str(path)], capture_output=True, text=True, timeout=20)
  except (OSError, subprocess.SubprocessError):
    return True
  return bool(proc.stdout.strip()) or proc.returncode != 1 or bool(proc.stderr.strip())


def session_key(path):
  # 子 agent 的檔跟著主 session：<session>/subagents/... 用 <session> 判斷是否還活著
  parts = path.relative_to(CC_ROOT).parts if path.is_relative_to(CC_ROOT) else path.parts
  if len(parts) >= 3:
    return parts[1]
  return path.stem


def pi_key(path):
  return path.stem.split("_")[-1]


def has_duplicates(path):
  try:
    with open(path, "rb") as handle:
      if os.fstat(handle.fileno()).st_size == 0:
        return False
      with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as view:
        # 只是快篩：已瘦過的占位紀錄是 api-request-pruned，不算；圖片副本會多抓，交給 session-slim 判斷，處理過記進 seen 不再重讀
        replay = any(view.find(marker) >= 0 for marker in (b'"api-request-blob"', b'"api-request-shape"', b'"type":"api-request"'))
        return replay or (view.find(b'"toolUseResult"') >= 0 and view.find(b'"base64"') >= 0)
  except OSError:
    return False


def load_seen(state):
  try:
    return json.loads((state / "seen.json").read_text())
  except (OSError, ValueError):
    return {}


def save_seen(state, seen):
  temp = state / "seen.json.tmp"
  temp.write_text(json.dumps(seen))
  os.replace(temp, state / "seen.json")


def take_queue(state):
  queue = state / "queue.jsonl"
  taking = state / "queue.taking.jsonl"
  try:
    os.replace(queue, taking)
  except FileNotFoundError:
    return []
  items = []
  for line in taking.read_text(errors="replace").splitlines():
    try:
      items.append(Path(json.loads(line)["path"]))
    except (ValueError, KeyError, TypeError):
      continue
  taking.unlink()
  return items


def ended(path, live, key, now, idle):
  try:
    info = path.stat()
  except OSError:
    return False
  return key not in live and now - info.st_mtime >= idle


def unchanged_since(seen, path):
  try:
    info = path.stat()
  except OSError:
    return True
  return seen.get(str(path)) == [info.st_size, info.st_mtime_ns]


def cc_candidates(seen, live, now, queued):
  picked = []
  for path in queued:
    group = [path] + sorted(path.with_suffix("").glob("**/*.jsonl"))
    picked.extend(p for p in group if ended(p, live, session_key(p), now, QUEUE_SETTLE_SECONDS))
  horizon = now - SWEEP_DAYS * 86400
  for path in CC_ROOT.glob("**/*.jsonl"):
    try:
      info = path.stat()
    except OSError:
      continue
    if info.st_mtime < horizon or info.st_size < MIN_BYTES:
      continue
    if ended(path, live, session_key(path), now, IDLE_SECONDS):
      picked.append(path)
  unique = []
  for path in picked:
    if path not in unique and not unchanged_since(seen, path) and has_duplicates(path):
      unique.append(path)
  return unique


def cc_cold_candidates(seen, live, now, exclude=()):
  picked = []
  for path in CC_ROOT.glob("**/*.jsonl"):
    if path in exclude:
      continue
    try:
      info = path.stat()
    except OSError:
      continue
    if now - info.st_mtime < COLD_DAYS * 86400 or info.st_size < MIN_BYTES or info.st_flags & stat.UF_COMPRESSED:
      continue
    if session_key(path) in live or unchanged_since(seen, path) or has_duplicates(path):
      continue
    picked.append(path)
  return picked


def pi_candidates(seen, live, now):
  horizon = now - SWEEP_DAYS * 86400
  picked = []
  for path in PI_ROOT.glob("**/*.jsonl"):
    try:
      info = path.stat()
    except OSError:
      continue
    if info.st_mtime < horizon or info.st_size < MIN_BYTES or info.st_flags & stat.UF_COMPRESSED:
      continue
    if ended(path, live, pi_key(path), now, IDLE_SECONDS) and not unchanged_since(seen, path) and not opened(path):
      picked.append(path)
  return picked


def mark(seen, path):
  try:
    info = path.stat()
  except OSError:
    return
  seen[str(path)] = [info.st_size, info.st_mtime_ns]


def compress_one(path, afsctool):
  info = path.stat()
  proc = subprocess.run([afsctool, "-c", "-T", "LZFSE", str(path)], capture_output=True, text=True)
  os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
  if proc.returncode != 0:
    detail = (proc.stderr or proc.stdout).strip() or f"afsctool exit {proc.returncode}"
    return {"path": str(path), "error": detail}
  return {"path": str(path), "compressed": bool(path.stat().st_flags & stat.UF_COMPRESSED)}


def run(state=STATE, audit_state=AUDIT_STATE, wait_queue=True):
  if (state / "disabled").exists():
    return {"skip": "disabled"}
  state.mkdir(parents=True, exist_ok=True)
  try:
    live = live_sessions()
  except LiveUnknown as error:
    return {"skip": f"live sessions unknown: {error}"}
  # 判斷得出誰還開著才取佇列：先取再放棄，SessionEnd 登記的項目會被刪掉卻沒處理
  queued = take_queue(state)
  deadline = time.time() + (QUEUE_WAIT_SECONDS if wait_queue and queued else 0)
  live_stale = False
  # SessionEnd 觸發時 CC 還沒完全退出：等它的程序結束、檔案靜下來，最多等兩分鐘，其餘交給每小時補漏
  while queued and time.time() < deadline and any(session_key(p) in live for p in queued):
    time.sleep(5)
    try:
      live = live_sessions()
      live_stale = False
    except LiveUnknown:
      live_stale = True
  seen = load_seen(state)
  now = time.time()
  afsctool = shutil.which("afsctool")
  cc = cc_candidates(seen, live, now, queued)
  # 等待佇列期間若新起 claude 的註冊檔尚未就緒，重開的 >30 天舊對話 mtime 還沒變，本輪先不壓冷檔
  cold_cc = cc_cold_candidates(seen, live, now, set(cc)) if afsctool and not live_stale else []
  pi = pi_candidates(seen, live, now) if afsctool else []
  results = []
  if cc:
    slim = load_slim()
    audit = slim.load_audit()
    handle = slim.acquire(audit_state / "run.lock", slim.LOCK_WAIT_SECONDS)
    if handle is None:
      results.append({"skip": "session-audit lock busy", "pending": len(cc)})
      cc = []
    try:
      for path in cc:
        try:
          result = slim.slim_one(path, audit_state, audit)
        except (OSError, ValueError) as error:
          result = {"path": str(path), "error": str(error)}
        results.append(result)
        if "error" not in result:
          mark(seen, path)
    finally:
      if handle is not None:
        handle.close()
  for path in cold_cc:
    try:
      result = dict(compress_one(path, afsctool), kind="cc_cold")
    except OSError as error:
      result = {"path": str(path), "kind": "cc_cold", "error": str(error)}
    results.append(result)
    if "error" not in result:
      mark(seen, path)
  for path in pi:
    try:
      result = compress_one(path, afsctool)
    except OSError as error:
      result = {"path": str(path), "error": str(error)}
    results.append(result)
    if "error" not in result:
      mark(seen, path)
  save_seen(state, seen)
  for result in results:
    append_line(state / "log.jsonl", dict(result, at=round(now)))
  return {"cc": len(cc), "cc_cold": len(cold_cc), "pi": len(pi), "queued": len(queued)}


def main(argv=None):
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("command", choices=("enqueue", "run"))
  args = parser.parse_args(argv)
  if args.command == "enqueue":
    try:
      enqueue(sys.stdin.read())
    except OSError:
      pass
    return 0
  print(json.dumps(run(), ensure_ascii=False))
  return 0


if __name__ == "__main__":
  sys.exit(main())
