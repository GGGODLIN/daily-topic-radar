#!/usr/bin/env python3
"""Run session-slim on ended Claude Code sessions, and compress cold plain-text CC and ended pi sessions.

Threat model: 這是給誠實系統用的自動整理，不是安全閘門；判斷不出 session 是否結束時一律不動，留給下一輪。
不保證：只認得程序名稱是 claude 的 Claude Code（用 node 跑的不算）；除已確認的非對話模式或已退出程序外，事件等待後仍無法對上
~/.claude/sessions 註冊檔的 claude 程序（例如另一個 CLAUDE_CONFIG_DIR）會讓整輪不動；pi 沒有註冊檔，只靠「沒被任何程序開著＋閒置」判斷；
程式被強制結束時 SessionEnd 可能沒觸發，由每小時補漏接手。
停用：建立 <state>/disabled。
"""
import argparse
import fcntl
import importlib.util
import json
import mmap
import os
import re
import select
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
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
# 實測撤註冊到退出約 1.03 秒；向上取整只限制等待，逾時後仍重新核對存活名單。
LIVE_TRANSITION_SECONDS = 2
SWEEP_DAYS = 14
COLD_DAYS = 30
MIN_BYTES = 256 * 1024
COMPRESS_TIMEOUT_SECONDS = 300


def load_slim():
  spec = importlib.util.spec_from_file_location("session_slim", HERE / "session-slim.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def append_line(path, record):
  # 只追加一般檔：佇列或紀錄被換成 symlink／FIFO 時直接放棄，不寫到別處、不卡住
  try:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
  except OSError:
    return False
  try:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
      return False
    os.write(fd, (json.dumps(record, ensure_ascii=False) + "\n").encode())
    return True
  except OSError:
    return False
  finally:
    os.close(fd)


def enqueue(stdin_text, state=STATE):
  try:
    payload = json.loads(stdin_text or "{}")
  except ValueError:
    return False
  if not isinstance(payload, dict):
    return False
  path = payload.get("transcript_path")
  if not isinstance(path, str) or not path or not path.endswith(".jsonl"):
    return False
  try:
    resolved = Path(path).resolve()
    resolved.relative_to(CC_ROOT.resolve())
  except (OSError, ValueError):
    return False
  return append_line(state / "queue.jsonl", {"path": str(resolved), "session_id": payload.get("session_id", ""), "at": time.time()})


class LiveUnknown(Exception):
  def __init__(self, message, pid=None):
    super().__init__(message)
    self.pid = pid


def _read_regular_bytes(path, nonblock=True):
  flags = os.O_RDONLY | os.O_NOFOLLOW | (os.O_NONBLOCK if nonblock else 0)
  try:
    fd = os.open(path, flags)
  except OSError:
    return None
  try:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
      return None
    chunks = []
    while True:
      chunk = os.read(fd, 65536)
      if not chunk:
        break
      chunks.append(chunk)
    return b"".join(chunks)
  except OSError:
    return None
  finally:
    os.close(fd)


def _read_registry_dict(reg_file):
  raw = _read_regular_bytes(reg_file)
  if raw is None:
    return None, None
  try:
    parsed = json.loads(raw.decode("utf-8"))
  except (UnicodeDecodeError, ValueError):
    return raw, None
  return raw, parsed if isinstance(parsed, dict) else None


def wait_for_registration_or_exit(pid, timeout=LIVE_TRANSITION_SECONDS):
  # 註冊檔與程序生命週期不同步；事件只喚醒重新查證，不能直接證明對話已結束。
  if timeout <= 0:
    return
  reg_file = REGISTRY / f"{pid}.json"
  initial_bytes = None
  dir_fd = None
  reg_fd = None
  watcher = None
  vnode_flags = (
    select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_ATTRIB
    | select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME
  )
  try:
    watcher = select.kqueue()
    try:
      watcher.control([
        select.kevent(pid, filter=select.KQ_FILTER_PROC, flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                      fflags=select.KQ_NOTE_EXIT),
      ], 0, 0)
    except OSError:
      if process_exited(pid):
        return
    if process_exited(pid):
      return
    try:
      dir_fd = os.open(REGISTRY, os.O_RDONLY)
    except OSError:
      dir_fd = None
    if dir_fd is not None:
      initial_bytes, _ = _read_registry_dict(reg_file)
      watcher.control([
        select.kevent(dir_fd, filter=select.KQ_FILTER_VNODE, flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                      fflags=select.KQ_NOTE_WRITE | select.KQ_NOTE_RENAME | select.KQ_NOTE_DELETE),
      ], 0, 0)
      try:
        reg_fd = os.open(reg_file, os.O_RDONLY | os.O_NOFOLLOW)
      except OSError:
        reg_fd = None
      if reg_fd is not None:
        watcher.control([
          select.kevent(reg_fd, filter=select.KQ_FILTER_VNODE, flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                        fflags=vnode_flags),
        ], 0, 0)
      now_bytes, now_dict = _read_registry_dict(reg_file)
      if now_dict is not None and (initial_bytes is None or now_bytes != initial_bytes):
        return
      if initial_bytes is None and now_bytes is not None and reg_fd is None:
        try:
          reg_fd = os.open(reg_file, os.O_RDONLY | os.O_NOFOLLOW)
          watcher.control([
            select.kevent(reg_fd, filter=select.KQ_FILTER_VNODE, flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                          fflags=vnode_flags),
          ], 0, 0)
        except OSError:
          pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
      events = watcher.control([], 4, max(0, deadline - time.monotonic()))
      if not events or any(event.filter == select.KQ_FILTER_PROC for event in events):
        return
      now_bytes, now_dict = _read_registry_dict(reg_file)
      if now_dict is not None and (initial_bytes is None or now_bytes != initial_bytes):
        return
      if now_bytes is not None and now_dict is None:
        if reg_fd is None:
          try:
            reg_fd = os.open(reg_file, os.O_RDONLY | os.O_NOFOLLOW)
            watcher.control([
              select.kevent(reg_fd, filter=select.KQ_FILTER_VNODE, flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                            fflags=vnode_flags),
            ], 0, 0)
          except OSError:
            pass
        rem = deadline - time.monotonic()
        if rem > 0:
          time.sleep(min(0.02, rem))
          now_bytes, now_dict = _read_registry_dict(reg_file)
          if now_dict is not None and (initial_bytes is None or now_bytes != initial_bytes):
            return
  except OSError:
    return
  finally:
    if watcher is not None:
      watcher.close()
    if reg_fd is not None:
      os.close(reg_fd)
    if dir_fd is not None:
      os.close(dir_fd)


def process_exited(pid):
  # ps 空回覆也可能是查詢失敗；只有核心確認 PID 已不存在才放行。
  try:
    os.kill(pid, 0)
  except ProcessLookupError:
    return True
  except OSError:
    return False
  return False


def is_non_session_process(match):
  # 查版本／說明和 Chrome 橋接沒有對話註冊檔；只認完整命令，prompt 裡的旗標不能豁免真正對話。
  try:
    proc = subprocess.run(["ps", "-p", match["pid"], "-o", "pid=,lstart=,args="], capture_output=True, text=True,
                          timeout=20, env=dict(os.environ, TZ="UTC", LC_ALL="C"), check=False)
  except (OSError, subprocess.SubprocessError):
    return False
  if proc.returncode == 1 and not proc.stdout.strip() and not proc.stderr.strip():
    return process_exited(int(match["pid"]))
  if proc.returncode != 0:
    return False
  detail = PS_LINE.fullmatch(proc.stdout.strip())
  flags = ("--chrome-native-host", "--version", "-v", "--help", "-h")
  same_identity = bool(detail and detail["pid"] == match["pid"]
                       and " ".join(detail["start"].split()) == " ".join(match["start"].split()))
  if same_identity and detail["comm"].strip() in {f'{match["comm"].strip()} {flag}' for flag in flags}:
    return True
  if detail:
    # 只記固定旗標與身分比對，避免把 -p 的 prompt 或憑證寫進排程日誌。
    mode = ",".join(flag for flag in (*flags, "-p", "--print") if flag in detail["comm"].split()) or "unknown"
    raise LiveUnknown(f'claude pid {match["pid"]} has no readable registry (flags={mode}, same_identity={same_identity})',
                      pid=int(match["pid"]))
  return False


def live_sessions():
  # 不問 Herdr：Herdr 沒在跑或換版時 agent list 拿不到，整個排程就停擺，而且不在 Herdr 裡的 session 根本不在名單上。
  # Claude Code 自己寫的 ~/.claude/sessions/<pid>.json 不依賴任何終端機；只豁免已確認的非對話模式或已退出程序。
  # 其他 claude（包含 -p 對話）對不上註冊檔仍丟 LiveUnknown，呼叫端整輪不動。
  try:
    proc = subprocess.run(["ps", "-axo", "pid=,lstart=,comm="], capture_output=True, text=True, timeout=20,
                          env=dict(os.environ, TZ="UTC", LC_ALL="C"), check=True)
  except (OSError, subprocess.SubprocessError) as error:
    raise LiveUnknown(f"ps failed: {error}") from error
  live = set()
  for line in proc.stdout.splitlines():
    match = PS_LINE.match(line)
    if not match:
      if line.rstrip().endswith(("/claude", " claude", "\tclaude")):
        raise LiveUnknown("unrecognized claude process row")
      continue
    if Path(match["comm"].strip()).name != "claude":
      continue
    pid = int(match["pid"])
    try:
      record = json.loads((REGISTRY / f"{pid}.json").read_text())
    except (OSError, ValueError) as error:
      if is_non_session_process(match):
        continue
      raise LiveUnknown(f"claude pid {pid} has no readable registry", pid=pid) from error
    if not isinstance(record, dict):
      raise LiveUnknown(f"claude pid {pid} registry is not a JSON object", pid=pid)
    # 比對啟動時間：pid 被重用時，舊程序留下的註冊檔不能拿來代表新程序
    started = " ".join(str(record.get("procStart", "")).split())
    if record.get("pid") != pid or started != " ".join(match["start"].split()) or not record.get("sessionId"):
      raise LiveUnknown(f"claude pid {pid} registry does not match the process", pid=pid)
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
  try:
    parts = path.resolve().relative_to(CC_ROOT.resolve()).parts
  except (OSError, ValueError):
    return path.stem
  if len(parts) >= 3:
    return parts[1]
  return path.stem


def pi_key(path):
  return path.stem.split("_")[-1]


def has_duplicates(path):
  try:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
  except OSError:
    return False
  try:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
      return False
    with mmap.mmap(fd, 0, access=mmap.ACCESS_READ) as view:
      # 只是快篩：已瘦過的占位紀錄是 api-request-pruned，不算；圖片副本會多抓，交給 session-slim 判斷，處理過記進 seen 不再重讀
      replay = (
        any(view.find(marker) >= 0 for marker in (b'"api-request-blob"', b'"api-request-shape"'))
        or re.search(rb'"type"[ \t]*:[ \t]*"api-request(?:-blob|-shape)?"', view) is not None
      )
      return replay or (view.find(b'"toolUseResult"') >= 0 and view.find(b'"base64"') >= 0)
  except OSError:
    return False
  finally:
    os.close(fd)


def load_seen(state):
  raw = _read_regular_bytes(state / "seen.json")
  if raw is None:
    return {}
  try:
    data = json.loads(raw.decode("utf-8"))
  except (UnicodeDecodeError, ValueError):
    return {}
  return data if isinstance(data, dict) else {}


def save_seen(state, seen):
  state.mkdir(parents=True, exist_ok=True)
  tmp_path = None
  try:
    with tempfile.NamedTemporaryFile(mode="w", dir=state, prefix=".seen.", suffix=".tmp",
                                     encoding="utf-8", delete=False) as handle:
      tmp_path = Path(handle.name)
      json.dump(seen, handle)
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, state / "seen.json")
  except Exception:
    if tmp_path is not None:
      try:
        tmp_path.unlink(missing_ok=True)
      except OSError:
        pass
    raise


def _valid_queue_path(entry):
  if not isinstance(entry, dict):
    return None
  raw = entry.get("path")
  if not isinstance(raw, str) or not raw or not raw.endswith(".jsonl"):
    return None
  try:
    resolved = Path(raw).resolve()
    resolved.relative_to(CC_ROOT.resolve())
  except (OSError, ValueError):
    return None
  return resolved


def _parse_queue_lines(lines):
  items = []
  seen = set()
  raw_lines = []
  for line in lines:
    try:
      entry = json.loads(line)
    except ValueError:
      continue
    resolved = _valid_queue_path(entry)
    if resolved is None or resolved in seen:
      continue
    seen.add(resolved)
    items.append(resolved)
    raw_lines.append(json.dumps({"path": str(resolved), "session_id": entry.get("session_id", ""), "at": entry.get("at", 0)},
                                ensure_ascii=False))
  return items, raw_lines


def _write_atomic(path, text):
  tmp_path = None
  try:
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
                                     encoding="utf-8", delete=False) as handle:
      tmp_path = Path(handle.name)
      handle.write(text)
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, path)
  except Exception:
    if tmp_path is not None:
      try:
        tmp_path.unlink(missing_ok=True)
      except OSError:
        pass
    raise


def _unlink_regular(path):
  try:
    if stat.S_ISREG(path.lstat().st_mode):
      path.unlink(missing_ok=True)
  except OSError:
    pass


def take_queue(state):
  queue = state / "queue.jsonl"
  taking = state / "queue.taking.jsonl"
  try:
    t_info = taking.lstat()
  except FileNotFoundError:
    t_info = None
  except OSError:
    return []
  if t_info is not None and not stat.S_ISREG(t_info.st_mode):
    raise OSError(f"not a regular queue file: {taking}")

  try:
    q_info = queue.lstat()
  except FileNotFoundError:
    q_info = None
  except OSError:
    raise
  if q_info is not None and not stat.S_ISREG(q_info.st_mode):
    raise OSError(f"not a regular queue file: {queue}")

  if t_info is None and q_info is None:
    return []

  if t_info is None:
    try:
      os.replace(queue, taking)
    except FileNotFoundError:
      return []
    raw = _read_regular_bytes(taking)
    if raw is None:
      return []
    items, _ = _parse_queue_lines(raw.decode("utf-8", errors="replace").splitlines())
    if not items:
      _unlink_regular(taking)
    return items

  t_raw = _read_regular_bytes(taking)
  t_lines = t_raw.decode("utf-8", errors="replace").splitlines() if t_raw is not None else []
  q_lines = []
  if q_info is not None:
    merge_tmp = None
    try:
      with tempfile.NamedTemporaryFile(dir=state, prefix=".queue.merge.", suffix=".jsonl", delete=False) as handle:
        merge_tmp = Path(handle.name)
      os.replace(queue, merge_tmp)
      q_raw = _read_regular_bytes(merge_tmp)
      if q_raw is not None:
        q_lines = q_raw.decode("utf-8", errors="replace").splitlines()
    except OSError:
      q_lines = []
    finally:
      if merge_tmp is not None:
        try:
          merge_tmp.unlink(missing_ok=True)
        except OSError:
          pass

  items, raw_lines = _parse_queue_lines(t_lines + q_lines)
  if not items:
    _unlink_regular(taking)
    return []
  if q_lines:
    _write_atomic(taking, "\n".join(raw_lines) + "\n")
  return items


def _requeue(state, paths):
  now = time.time()
  for path in paths:
    append_line(state / "queue.jsonl", {"path": str(path), "session_id": "", "at": now})


def ended(path, live, key, now, idle):
  try:
    info = path.lstat()
  except OSError:
    return False
  if not stat.S_ISREG(info.st_mode):
    return False
  return key not in live and now - info.st_mtime >= idle


def unchanged_since(seen, path):
  try:
    info = path.lstat()
  except OSError:
    return True
  if not stat.S_ISREG(info.st_mode):
    return True
  try:
    resolved_key = str(path.resolve())
  except OSError:
    resolved_key = None
  if resolved_key is not None and resolved_key in seen:
    val = seen.get(resolved_key)
  else:
    val = seen.get(str(path))
  return val == [info.st_size, info.st_mtime_ns]


def cc_candidates(seen, live, now, queued):
  picked = []
  for path in queued:
    group = [path] + sorted(path.with_suffix("").glob("**/*.jsonl"))
    picked.extend(p for p in group if ended(p, live, session_key(p), now, QUEUE_SETTLE_SECONDS))
  horizon = now - SWEEP_DAYS * 86400
  for path in CC_ROOT.glob("**/*.jsonl"):
    try:
      info = path.lstat()
    except OSError:
      continue
    if not stat.S_ISREG(info.st_mode) or info.st_mtime < horizon or info.st_size < MIN_BYTES:
      continue
    if ended(path, live, session_key(path), now, IDLE_SECONDS):
      picked.append(path)
  unique = []
  seen_resolved = set()
  for path in picked:
    try:
      resolved = path.resolve()
    except OSError:
      continue
    if resolved not in seen_resolved and not unchanged_since(seen, path) and has_duplicates(path):
      seen_resolved.add(resolved)
      unique.append(path)
  return unique


def cc_cold_candidates(seen, live, now, exclude=()):
  excluded = set()
  for item in exclude:
    try:
      excluded.add(Path(item).resolve())
    except OSError:
      excluded.add(Path(item))
  picked = []
  for path in CC_ROOT.glob("**/*.jsonl"):
    try:
      resolved = path.resolve()
    except OSError:
      continue
    if resolved in excluded:
      continue
    try:
      info = path.lstat()
    except OSError:
      continue
    if not stat.S_ISREG(info.st_mode):
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
      info = path.lstat()
    except OSError:
      continue
    if not stat.S_ISREG(info.st_mode):
      continue
    if info.st_mtime < horizon or info.st_size < MIN_BYTES or info.st_flags & stat.UF_COMPRESSED:
      continue
    if ended(path, live, pi_key(path), now, IDLE_SECONDS) and not unchanged_since(seen, path) and not opened(path):
      picked.append(path)
  return picked


def mark(seen, path):
  try:
    info = path.lstat()
  except OSError:
    return
  if not stat.S_ISREG(info.st_mode):
    return
  try:
    key = str(path.resolve())
  except OSError:
    key = str(path)
  seen[key] = [info.st_size, info.st_mtime_ns]


def _file_stamp(path):
  try:
    info = path.lstat()
  except OSError:
    return None
  if not stat.S_ISREG(info.st_mode):
    return None
  return (info.st_ino, info.st_size, info.st_mtime_ns)


def compress_one(path, afsctool):
  info = path.lstat()
  if not stat.S_ISREG(info.st_mode):
    return {"path": str(path), "error": "not a regular file"}
  try:
    proc = subprocess.run([afsctool, "-c", "-T", "LZFSE", str(path)], capture_output=True, text=True,
                          timeout=COMPRESS_TIMEOUT_SECONDS)
  except (OSError, subprocess.SubprocessError) as error:
    return {"path": str(path), "error": str(error)}
  if proc.returncode != 0:
    detail = (proc.stderr or proc.stdout).strip() or f"afsctool exit {proc.returncode}"
    return {"path": str(path), "error": detail}
  os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns), follow_symlinks=False)
  return {"path": str(path), "compressed": bool(path.lstat().st_flags & stat.UF_COMPRESSED)}


def _resolve_live_sessions():
  deadline = time.monotonic() + LIVE_TRANSITION_SECONDS
  last_pid = None
  same_pid_retries = 0
  while True:
    try:
      return live_sessions()
    except LiveUnknown as error:
      remaining = deadline - time.monotonic()
      if error.pid is None or remaining <= 0:
        raise
      if error.pid == last_pid:
        same_pid_retries += 1
        if same_pid_retries >= 1:
          raise
      else:
        last_pid = error.pid
        same_pid_retries = 0
      wait_for_registration_or_exit(error.pid, timeout=remaining)


def run(state=STATE, audit_state=AUDIT_STATE, wait_queue=True):
  if (state / "disabled").exists():
    return {"skip": "disabled"}
  state.mkdir(parents=True, exist_ok=True)
  try:
    lock_fd = os.open(state / "sweep.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
  except OSError:
    return {"skip": "sweep lock busy"}
  try:
    try:
      fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
      return {"skip": "sweep lock busy"}

    try:
      live = _resolve_live_sessions()
    except LiveUnknown as error:
      return {"skip": f"live sessions unknown: {error}"}

    # 判斷得出誰還開著才取佇列：先取再放棄，SessionEnd 登記的項目會被刪掉卻沒處理
    try:
      queued = take_queue(state)
    except OSError as error:
      return {"skip": f"queue unavailable: {error}"}
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

    if live_stale:
      try:
        live = _resolve_live_sessions()
        live_stale = False
      except LiveUnknown as error:
        _requeue(state, queued)
        _unlink_regular(state / "queue.taking.jsonl")
        return {"skip": f"live sessions unknown: {error}"}

    seen = load_seen(state)
    now = time.time()
    afsctool = shutil.which("afsctool")
    cc = cc_candidates(seen, live, now, queued)
    # 等待佇列期間若新起 claude 的註冊檔尚未就緒，重開的 >30 天舊對話 mtime 還沒變，本輪先不壓冷檔
    cold_cc = cc_cold_candidates(seen, live, now, set(cc)) if afsctool and not live_stale else []
    pi = pi_candidates(seen, live, now) if afsctool else []
    cold_stamps = {path: _file_stamp(path) for path in cold_cc}
    pi_stamps = {path: _file_stamp(path) for path in pi}

    results = []
    requeue_items = []
    cc_attempted = bool(cc)
    if cc:
      slim = load_slim()
      audit = slim.load_audit()
      handle = slim.acquire(audit_state / "run.lock", slim.LOCK_WAIT_SECONDS)
      if handle is None:
        results.append({"skip": "session-audit lock busy", "pending": len(cc)})
        pending_resolved = set()
        for p in cc:
          try:
            pending_resolved.add(p.resolve())
          except OSError:
            pass
        for q in queued:
          group = [q] + sorted(q.with_suffix("").glob("**/*.jsonl"))
          if any(g.resolve() in pending_resolved for g in group if g.exists()) or not ended(q, live, session_key(q), now, QUEUE_SETTLE_SECONDS):
            requeue_items.append(q)
        cc = []
      try:
        if handle is not None:
          try:
            live = _resolve_live_sessions()
            cc = cc_candidates(seen, live, time.time(), queued)
          except LiveUnknown as error:
            results.append({"skip": f"live sessions unknown: {error}"})
            requeue_items.extend(queued)
            cc = []
        for path in cc:
          try:
            result = slim.slim_one(path, audit_state, audit)
          except (OSError, ValueError, sqlite3.Error) as error:
            result = {"path": str(path), "error": str(error)}
          results.append(result)
          if "error" not in result:
            mark(seen, path)
      finally:
        if handle is not None:
          handle.close()

    if cc_attempted and (cold_cc or pi):
      try:
        live = _resolve_live_sessions()
      except LiveUnknown:
        cold_cc = []
        pi = []

    if cold_cc:
      verified_cold = []
      for path in cold_cc:
        stamp = cold_stamps.get(path)
        if stamp is not None and _file_stamp(path) == stamp and session_key(path) not in live:
          verified_cold.append(path)
      cold_cc = verified_cold
    for path in cold_cc:
      try:
        result = dict(compress_one(path, afsctool), kind="cc_cold")
      except OSError as error:
        result = {"path": str(path), "kind": "cc_cold", "error": str(error)}
      results.append(result)
      if "error" not in result:
        mark(seen, path)

    if pi:
      verified_pi = []
      for path in pi:
        stamp = pi_stamps.get(path)
        if stamp is not None and _file_stamp(path) == stamp and not opened(path):
          verified_pi.append(path)
      pi = verified_pi
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
    for q in queued:
      if q not in requeue_items and _file_stamp(q) is not None and not ended(q, live, session_key(q), now, QUEUE_SETTLE_SECONDS) and not unchanged_since(seen, q):
        requeue_items.append(q)
    if requeue_items:
      _requeue(state, requeue_items)
    _unlink_regular(state / "queue.taking.jsonl")
    return {"cc": len(cc), "cc_cold": len(cold_cc), "pi": len(pi), "queued": len(queued)}
  finally:
    try:
      fcntl.flock(lock_fd, fcntl.LOCK_UN)
    except OSError:
      pass
    os.close(lock_fd)


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
