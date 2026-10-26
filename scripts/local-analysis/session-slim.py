#!/usr/bin/env python3
"""Shrink ended Claude Code transcripts without making session-audit start over.

拿掉的只有重複內容：CC request-replay 紀錄（api-request*）與 toolUseResult 裡的圖片副本（同一張圖已在 message.content）。
每行保留、只縮內容，行號不變；session-audit 的分析文件除了 `offset=` 數字外一字不差，所以把它記的位置換算到新檔即可續跑。
"""
import argparse
import bisect
import fcntl
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPLAY_STUB = b'{"type":"api-request-pruned"}'
IMAGE_STUB = b'{"pruned":"image"}'
KEY = b'"toolUseResult":'
OFFSET_RE = re.compile(rb"(?<=offset=)\d+")
TAIL_BYTES = 4096
LOCK_WAIT_SECONDS = 20 * 60
DECODER = json.JSONDecoder()


def load_audit():
  spec = importlib.util.spec_from_file_location("session_audit", HERE / "session-audit.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def has_image(value):
  text = json.dumps(value)
  return '"base64"' in text or '"image/' in text


def slim_line(line):
  body = line.rstrip(b"\n")
  tail = line[len(body):]
  if b'"api-request' in body[:200]:
    try:
      kind = str(json.loads(body).get("type", ""))
    except ValueError:
      return line
    if kind.startswith("api-request") and len(body) > len(REPLAY_STUB):
      return REPLAY_STUB + tail
    return line
  at = body.find(KEY)
  if at < 0:
    return line
  # 先前同長度補空白的版本（占位物件後面跟一串空白）也收回來
  start = at + len(KEY)
  while body[start:start + 1] in (b" ", b"\t"):
    start += 1
  if body.startswith(IMAGE_STUB, start):
    end = start + len(IMAGE_STUB)
    pad = len(body[end:]) - len(body[end:].lstrip(b" "))
    return (body[:end] + body[end + pad:] + tail) if pad else line
  if b"base64" not in body:
    return line
  text = body.decode("utf-8")
  char_start = len(body[:start].decode("utf-8"))
  value, char_end = DECODER.raw_decode(text, char_start)
  if not has_image(value):
    return line
  end = len(text[:char_end].encode("utf-8"))
  if end - start <= len(IMAGE_STUB):
    return line
  return body[:start] + IMAGE_STUB + body[end:] + tail


def line_starts(raw):
  starts = [0]
  at = raw.find(b"\n")
  while at >= 0:
    starts.append(at + 1)
    at = raw.find(b"\n", at + 1)
  return starts


class Plan:
  def __init__(self, raw, new, old_doc, new_doc):
    self.raw, self.new = raw, new
    self.old_doc, self.new_doc = old_doc, new_doc
    self.old_spans = [m.span() for m in OFFSET_RE.finditer(old_doc)]
    self.new_spans = [m.span() for m in OFFSET_RE.finditer(new_doc)]
    self.old_ends = [end for _, end in self.old_spans]
    self.new_ends = [end for _, end in self.new_spans]
    self.cut, self.old_digits, self.new_digits = [0], [0], [0]
    for (os_, oe), (ns, ne) in zip(self.old_spans, self.new_spans):
      self.cut.append(self.cut[-1] + (oe - os_) - (ne - ns))
      self.old_digits.append(self.old_digits[-1] + oe - os_)
      self.new_digits.append(self.new_digits[-1] + ne - ns)
    self.old_lines, self.new_lines = line_starts(raw), line_starts(new)

  def doc(self, position):
    # 位置之前已經結束的 offset 數字各自縮了幾位，往前挪那麼多；落在數字中間就停在新數字裡同一處（數字變短則停在尾端）
    index = bisect.bisect_right(self.old_ends, position)
    if index < len(self.old_spans) and self.old_spans[index][0] < position:
      start, end = self.new_spans[index]
      return start + min(position - self.old_spans[index][0], end - start)
    return position - self.cut[index]

  def plain(self, position, spans, ends, digits):
    # 拿掉所有 offset 數字後的座標；新舊文件除數字外一字不差，同一處換算前後座標必須相同
    index = bisect.bisect_right(ends, position)
    inside = max(0, position - spans[index][0]) if index < len(spans) else 0
    return position - digits[index] - inside

  def raw_at(self, position):
    index = bisect.bisect_right(self.old_lines, position) - 1
    if self.old_lines[index] == position:
      return self.new_lines[index]
    return None


def plan_file(raw, audit):
  new = b"".join(slim_line(line) for line in raw.splitlines(keepends=True))
  if new == raw:
    return None, "nothing to remove"
  if raw.count(b"\n") != new.count(b"\n"):
    return None, "line count changed"
  for line in new.splitlines():
    if line.strip():
      json.loads(line)
  old_doc = audit.build_document(raw)[0].encode()
  new_doc = audit.build_document(new)[0].encode()
  if OFFSET_RE.sub(b"", old_doc) != OFFSET_RE.sub(b"", new_doc):
    return None, "analysis document changed beyond offsets"
  if len(OFFSET_RE.findall(old_doc)) != len(OFFSET_RE.findall(new_doc)):
    return None, "offset markers differ"
  return Plan(raw, new, old_doc, new_doc), None


def same_span(plan, start, end):
  for position in (min(start, len(plan.old_doc)), min(end, len(plan.old_doc))):
    old = plan.plain(position, plan.old_spans, plan.old_ends, plan.old_digits)
    new = plan.plain(plan.doc(position), plan.new_spans, plan.new_ends, plan.new_digits)
    if old != new:
      return False
  return True


def tail_of(data, end):
  return bytes(data[max(0, end - TAIL_BYTES):end])


def rebase(connection, inode, plan):
  row = connection.execute(
    "SELECT generation, file_size, doc_offset, doc_tail, status FROM sources WHERE inode = ?", (inode,)
  ).fetchone()
  if row is None or row[1] is None:
    return "no audit progress"
  generation, file_size, doc_offset, doc_tail, status = row
  doc_offset = doc_offset or 0
  if doc_offset > len(plan.old_doc) or tail_of(plan.old_doc, doc_offset) != doc_tail:
    # 分析文件的格式改過（例如 10-25 起略過重播），舊進度本來就對不上現在的文件。
    # 只有「整份分析完、之後沒再寫」的才能直接記成新文件已讀完；其他情況動了會整份重送，不瘦。
    if status in ("complete", "partial") and file_size == len(plan.raw):
      end = len(plan.new_doc)
      connection.execute(
        "UPDATE sources SET file_size = ?, file_tail = ?, doc_offset = ?, doc_tail = ? WHERE inode = ?",
        (len(plan.new), tail_of(plan.new, len(plan.new)), end, tail_of(plan.new_doc, end), inode),
      )
      return None
    return "analyzed with an older document format"
  new_size = plan.raw_at(file_size) if file_size <= len(plan.raw) else None
  if new_size is None:
    return "analyzed size is not a line boundary"
  # 換算錯一格，session-audit 就會從錯的地方續跑；寫入前逐段對過，對不上就整份不動。
  spans = [(0, doc_offset)] + [tuple(item) for item in connection.execute(
    "SELECT doc_start, doc_end FROM segments WHERE inode = ? AND generation = ?", (inode, generation)
  )]
  if not all(same_span(plan, start, end) for start, end in spans):
    return "mapped spans do not match"
  new_offset = plan.doc(doc_offset)
  connection.execute(
    "UPDATE sources SET file_size = ?, file_tail = ?, doc_offset = ?, doc_tail = ? WHERE inode = ?",
    (new_size, tail_of(plan.new, new_size), new_offset, tail_of(plan.new_doc, new_offset), inode),
  )
  # 新位置一律不大於舊位置且保持順序：由小到大逐列改，主鍵 (inode, generation, start) 不會撞到還沒改的列。
  for table, key, others in (
    ("doc_blocks", "block_start", ("block_end", "cursor")),
    ("model_replies", "doc_start", ("doc_end",)),
    ("rule_coverage", "doc_start", ()),
  ):
    columns = ", ".join((key,) + others)
    rows = connection.execute(
      f"SELECT {columns} FROM {table} WHERE inode = ? AND generation = ? ORDER BY {key}", (inode, generation)
    ).fetchall()
    for values in rows:
      assignments = ", ".join(f"{name} = ?" for name in (key,) + others)
      connection.execute(
        f"UPDATE {table} SET {assignments} WHERE inode = ? AND generation = ? AND {key} = ?",
        (*[plan.doc(value) for value in values], inode, generation, values[0]),
      )
  for segment_id, start, end, byte_offset in connection.execute(
    "SELECT id, doc_start, doc_end, byte_offset FROM segments WHERE inode = ? AND generation = ?", (inode, generation)
  ).fetchall():
    moved = plan.raw_at(byte_offset)
    connection.execute(
      "UPDATE segments SET doc_start = ?, doc_end = ?, byte_offset = ? WHERE id = ?",
      (plan.doc(start), plan.doc(end), byte_offset if moved is None else moved, segment_id),
    )
  return None


def acquire(lock_path, wait):
  lock_path.parent.mkdir(parents=True, exist_ok=True)
  handle = open(lock_path, "a+")
  deadline = time.monotonic() + wait
  while True:
    try:
      fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
      return handle
    except BlockingIOError:
      if time.monotonic() > deadline:
        handle.close()
        return None
      time.sleep(2)


def compress(path, atime_ns, mtime_ns):
  if subprocess.run(["which", "afsctool"], capture_output=True).returncode == 0:
    subprocess.run(["afsctool", "-c", "-T", "LZFSE", str(path)], capture_output=True)
  os.utime(path, ns=(atime_ns, mtime_ns))


def slim_one(path, state, audit, do_compress=True, dry_run=False):
  # 呼叫端必須已持有 state/run.lock：session-audit 的 run 全程拿著它，持鎖期間它不會讀寫這些進度。
  before = os.stat(path)
  raw = path.read_bytes()
  plan, reason = plan_file(raw, audit)
  if plan is None:
    return {"path": str(path), "skip": reason}
  now = os.stat(path)
  if (now.st_ino, now.st_size, now.st_mtime_ns) != (before.st_ino, before.st_size, before.st_mtime_ns):
    return {"path": str(path), "skip": "changed while planning"}
  database = state / "state.sqlite"
  connection = sqlite3.connect(database, timeout=60) if database.exists() else None
  note = "no audit state"
  try:
    if connection is not None:
      connection.execute("BEGIN IMMEDIATE")
      note = rebase(connection, str(before.st_ino), plan)
      if note not in (None, "no audit progress"):
        connection.rollback()
        return {"path": str(path), "skip": note}
      if dry_run:
        connection.rollback()
    if dry_run:
      return {"path": str(path), "before": len(raw), "after": len(plan.new), "dry_run": True, "audit": note or "rebased"}
    with open(path, "r+b") as handle_file:
      handle_file.write(plan.new)
      handle_file.truncate(len(plan.new))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    if connection is not None:
      connection.commit()
  finally:
    if connection is not None:
      connection.close()
  if do_compress:
    compress(path, before.st_atime_ns, before.st_mtime_ns)
  return {"path": str(path), "before": len(raw), "after": len(plan.new)}


def main(argv=None):
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("paths", nargs="+", type=Path)
  parser.add_argument("--state", type=Path, required=True)
  parser.add_argument("--lock-wait", type=float, default=LOCK_WAIT_SECONDS)
  parser.add_argument("--no-compress", action="store_true")
  parser.add_argument("--dry-run", action="store_true", help="plan, rebase inside a rolled-back transaction, write nothing")
  args = parser.parse_args(argv)
  audit = load_audit()
  # 整批只拿一次鎖：逐份搶會每份都等 session-audit 跑完一輪（最長 15 分鐘）
  handle = acquire(args.state / "run.lock", args.lock_wait)
  if handle is None:
    print(json.dumps({"skip": "session-audit lock busy"}), flush=True)
    return 1
  failed = False
  try:
    for path in args.paths:
      try:
        result = slim_one(path, args.state, audit, not args.no_compress, args.dry_run)
      except (OSError, ValueError, sqlite3.Error) as error:
        result, failed = {"path": str(path), "error": str(error)}, True
      print(json.dumps(result, ensure_ascii=False), flush=True)
  finally:
    handle.close()
  return 1 if failed else 0


if __name__ == "__main__":
  sys.exit(main())
