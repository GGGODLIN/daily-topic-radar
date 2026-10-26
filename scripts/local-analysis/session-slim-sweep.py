#!/usr/bin/env python3
"""Run session-slim on Claude Code sessions that have ended, and compress ended pi sessions.

Threat model: 這是給誠實系統用的自動整理，不是安全閘門；判斷不出 session 是否結束時一律不動，留給下一輪。
不保證：Herdr 以外開的 session 只靠閒置時間判斷；程式被強制結束時 SessionEnd 可能沒觸發，由每小時補漏接手。
停用：建立 <state>/disabled。
"""
import argparse
import importlib.util
import json
import mmap
import os
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
IDLE_SECONDS = 30 * 60
QUEUE_SETTLE_SECONDS = 60
QUEUE_WAIT_SECONDS = 120
SWEEP_DAYS = 14
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


def live_sessions():
  # 回傳 Herdr 裡還活著的 agent 的 session id／路徑；拿不到就回 None，呼叫端整輪不動
  try:
    proc = subprocess.run(["herdr", "agent", "list"], capture_output=True, text=True, timeout=20)
    agents = json.loads(proc.stdout)["result"]["agents"]
  except (OSError, ValueError, KeyError, subprocess.SubprocessError):
    return None
  live = set()
  for agent in agents:
    value = str((agent.get("agent_session") or {}).get("value") or "")
    if value:
      # claude 給 session id；pi 有時給 id、有時給檔案路徑（檔名是「時間_id」），兩種都記
      stem = Path(value).stem if value.endswith(".jsonl") else value
      live.update({stem, stem.split("_")[-1]})
  return live


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
    if ended(path, live, pi_key(path), now, IDLE_SECONDS) and not unchanged_since(seen, path):
      picked.append(path)
  return picked


def mark(seen, path):
  try:
    info = path.stat()
  except OSError:
    return
  seen[str(path)] = [info.st_size, info.st_mtime_ns]


def run(state=STATE, audit_state=AUDIT_STATE, wait_queue=True):
  if (state / "disabled").exists():
    return {"skip": "disabled"}
  state.mkdir(parents=True, exist_ok=True)
  queued = take_queue(state)
  live = live_sessions()
  if live is None:
    return {"skip": "herdr agent list unavailable"}
  deadline = time.time() + (QUEUE_WAIT_SECONDS if wait_queue and queued else 0)
  # SessionEnd 觸發時 CC 還沒完全退出：等它離開 Herdr 名單、檔案靜下來，最多等兩分鐘，其餘交給每小時補漏
  while queued and time.time() < deadline and any(session_key(p) in live for p in queued):
    time.sleep(5)
    live = live_sessions() or live
  seen = load_seen(state)
  now = time.time()
  cc = cc_candidates(seen, live, now, queued)
  pi = pi_candidates(seen, live, now)
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
  for path in pi:
    info = path.stat()
    subprocess.run(["afsctool", "-c", "-T", "LZFSE", str(path)], capture_output=True)
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    results.append({"path": str(path), "compressed": bool(path.stat().st_flags & stat.UF_COMPRESSED)})
    mark(seen, path)
  save_seen(state, seen)
  for result in results:
    append_line(state / "log.jsonl", dict(result, at=round(now)))
  return {"cc": len(cc), "pi": len(pi), "queued": len(queued)}


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
