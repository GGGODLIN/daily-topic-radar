#!/usr/bin/env python3
"""recurring-errors-extract.py — recurring-errors channel 的確定性前段。

2026-08-01 從 recurring-errors-extract.sh 改寫。改寫理由不是「python 比較好」，是舊版的
bug 內生於它的結構：`jq ... | while IFS= read -r err` 按**行**迭代，一個多行錯誤訊息就被
拆成 N 個獨立簽名。實測 13 個錯誤產生 124 個簽名（9.5x），且排除首跑回填後的 top 15 裡有
11 個是同一則 dcg 攔截訊息的碎片——一次攔截佔掉 11 個名額，真正的行為問題被擠到第 9。
按行迭代要修就得脫離 shell 的 read 迴圈，所以整支改寫。

三個修正：
1. 一個錯誤 = 一個簽名（錯誤文字內的換行先壓成空白再正規化）
2. 日期用該筆錯誤的真實 timestamp，不是掃描當天（舊版 `date +%Y-%m-%d` 讓同一次回填的
   六個月資料全部標成同一天，趨勢與「跨多日」判斷都失真）
3. 去重用記憶體 set，不是對整個 ledger 跑 grep（舊版 O(n*m)，全量重建會慢到不可用）

正規化語意與舊版逐項對齊：小寫 → 去路徑 → 去長 hex → 去數字 → 壓空白 → 截 140 字 →
去頭尾空白 → 短於 15 字元丟棄。短簽名門檻是 `exit code` / `---` / `total` 這類碎片的濾網。

用法：
  python3 recurring-errors-extract.py            # 增量（state file 之後的新檔）
  python3 recurring-errors-extract.py --rebuild  # 全量重建（回看 180 天、覆寫 ledger）
  --days N     回看天數（預設增量吃 state、rebuild 吃 180）
  --dry-run    只印統計不寫檔
"""

import argparse
import json
import marshal
import math
import os
import re
import signal
import struct
import sys
import tempfile
import time
from pathlib import Path

if sys.flags.safe_path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from session_jsonl import discover_session_jsonl_files, parse_iso_utc

HOME = Path.home()
PROJECTS = HOME / ".claude" / "projects"
STATE = HOME / ".claude" / "state" / "recurring-errors.last_run"
LEDGER = HOME / "code" / "social-info" / "reports" / "local-analysis" / "recurring-errors-ledger.jsonl"

MIN_SIG_LEN = 15
MAX_SIG_LEN = 140

RE_PATH = re.compile(r"/\S+")
RE_HEX = re.compile(r"[0-9a-f]{8,}")
RE_NUM = re.compile(r"\d+")
RE_WS = re.compile(r"\s+")
RE_CLEAN_ASCII = re.compile(r"/\S+|[0-9a-f]{8,}|[0-9]+")
RE_CLEAN_UNI = re.compile(r"/\S+|[0-9a-f]{8,}|[0-9]+|[^\x00-\x7f\D]+")

_CHUNK_SIZE = 512 * 1024
_JSON_DECODE = json.JSONDecoder().decode
_FORK_MIN_FILES = 16
_MAX_WORKERS = 8
_FRAME_HDR = struct.Struct(">II")


def normalize(text):
    if not isinstance(text, str):
        return ""
    if len(text) > 512:
        m = RE_WS.search(text, 512)
        if m is not None:
            s = text[: m.start()].lower()
            s = (RE_CLEAN_ASCII if s.isascii() else RE_CLEAN_UNI).sub("", s)
            s = " ".join(s.split())
            if len(s) >= MAX_SIG_LEN:
                return s[:MAX_SIG_LEN].strip()
    s = text.lower()
    s = (RE_CLEAN_ASCII if s.isascii() else RE_CLEAN_UNI).sub("", s)
    s = " ".join(s.split())
    return s[:MAX_SIG_LEN].strip()


def error_text(block):
    c = block.get("content")
    if isinstance(c, list):
        return " ".join(
            x["text"]
            for x in c
            if isinstance(x, dict) and x.get("type") == "text" and isinstance(x.get("text"), str)
        )
    return "" if c is None else str(c)


def _extract_date(ts):
    d = parse_iso_utc(ts)
    return "" if d is None else d.date().isoformat()


def _yield_from_line(raw_line):
    if b'"is_error"' not in raw_line:
        return
    try:
        d = _JSON_DECODE(raw_line.decode("utf-8", "replace"))
    except Exception:
        return
    if not isinstance(d, dict) or d.get("type") != "user":
        return
    msg = d.get("message")
    if not isinstance(msg, dict):
        return
    content = msg.get("content")
    if not isinstance(content, list):
        return
    date_s = None
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") != "tool_result" or b.get("is_error") is not True:
            continue
        txt = error_text(b)
        if txt:
            if date_s is None:
                date_s = _extract_date(d.get("timestamp"))
            tuid = b.get("tool_use_id")
            tuid_s = tuid if isinstance(tuid, str) else ""
            yield date_s, txt, tuid_s


def _scan_chunk(data, limit):
    pos = 0
    while True:
        idx = data.find(b"true", pos, limit)
        if idx == -1:
            break
        j = idx - 1
        while j >= 0 and (data[j] == 32 or data[j] == 9):
            j -= 1
        if j >= 0 and data[j] == 58:  # ':'
            j -= 1
            while j >= 0 and (data[j] == 32 or data[j] == 9):
                j -= 1
            if j > 0 and data[j] == 34 and data[j - 1] != 92:
                if (
                    (j >= 9 and data[j - 9 : j + 1] == b'"is_error"')
                    or b"\\u" in data[max(0, j - 48) : j]
                ):
                    line_start = data.rfind(b"\n", 0, idx) + 1
                    line_end = data.find(b"\n", idx + 4, limit)
                    if line_end == -1:
                        line_end = limit
                    raw_line = data[line_start:line_end]
                    cr = raw_line.find(b"\r")
                    if cr != -1 and cr < len(raw_line) - 1:
                        for sub in raw_line.split(b"\r"):
                            if sub:
                                yield from _yield_from_line(sub)
                    else:
                        yield from _yield_from_line(raw_line)
                    pos = line_end + 1
                    if pos >= limit:
                        break
                    continue
        pos = idx + 4


def _iter_error_records_with_buf(path, buf):
    cap = len(buf)
    mv = memoryview(buf)
    try:
        with open(path, "rb", buffering=0) as f:
            rem_len = 0
            while True:
                n = f.readinto(mv[rem_len:] if rem_len else mv)
                if not n:
                    if rem_len:
                        yield from _scan_chunk(buf, rem_len)
                    break
                total = rem_len + n
                last_nl = buf.rfind(b"\n", 0, total)
                if last_nl == -1:
                    if total < cap:
                        rem_len = total
                        continue
                    parts = [bytes(buf)]
                    while True:
                        n2 = f.readinto(mv)
                        if not n2:
                            long_data = b"".join(parts)
                            yield from _scan_chunk(long_data, len(long_data))
                            return
                        nl2 = buf.find(b"\n", 0, n2)
                        if nl2 != -1:
                            parts.append(bytes(mv[: nl2 + 1]))
                            long_data = b"".join(parts)
                            yield from _scan_chunk(long_data, len(long_data))
                            rem_len = n2 - (nl2 + 1)
                            if rem_len:
                                # Assign through memoryview so overlapping shifts use memmove.
                                mv[:rem_len] = mv[nl2 + 1 : n2]
                            break
                        parts.append(bytes(mv[:n2]))
                    continue
                limit = last_nl + 1
                yield from _scan_chunk(buf, limit)
                rem_len = total - limit
                if rem_len:
                    mv[:rem_len] = mv[limit:total]
    except OSError:
        return
    finally:
        mv.release()


def _iter_error_records(path):
    yield from _iter_error_records_with_buf(path, bytearray(65536))


def _scan_one_file(path, buf):
    raw_count = 0
    recs = []
    for date, txt, tuid in _iter_error_records_with_buf(path, buf):
        raw_count += 1
        sig = normalize(txt)
        if len(sig) >= MIN_SIG_LEN:
            recs.append((date, sig, tuid))
    return raw_count, recs


def _write_all(fd, data):
    mv = memoryview(data)
    written = 0
    total = len(mv)
    while written < total:
        n = os.write(fd, mv[written:])
        if n <= 0:
            raise OSError("short write to pipe")
        written += n


def _read_exact(fd, size):
    if size == 0:
        return b""
    chunks = []
    rem = size
    while rem > 0:
        data = os.read(fd, rem)
        if not data:
            return None
        chunks.append(data)
        rem -= len(data)
    return chunks[0] if len(chunks) == 1 else b"".join(chunks)


def _iter_files_parallel(files, n_workers):
    """Stream per-file marshal frames from forked workers in exact index order (O(1 file) RAM)."""
    sys.stdout.flush()
    sys.stderr.flush()

    workers = []  # list of [pid_or_None, rfd_or_None, alive_bool]
    try:
        for w in range(n_workers):
            try:
                rfd, wfd = os.pipe()
            except OSError:
                workers.append([None, None, False])
                continue
            try:
                pid = os.fork()
            except OSError:
                os.close(rfd)
                os.close(wfd)
                workers.append([None, None, False])
                continue
            if pid == 0:
                rc = 0
                try:
                    os.close(rfd)
                    for _, prev_rfd, _ in workers:
                        if prev_rfd is not None:
                            os.close(prev_rfd)
                    buf = bytearray(_CHUNK_SIZE)
                    for idx in range(w, len(files), n_workers):
                        raw_count, recs = _scan_one_file(files[idx], buf)
                        payload = marshal.dumps((raw_count, recs)) if raw_count else b""
                        _write_all(wfd, _FRAME_HDR.pack(idx, len(payload)) + payload)
                except BaseException:
                    rc = 1
                finally:
                    try:
                        os.close(wfd)
                    except OSError:
                        pass
                    os._exit(rc)
            else:
                os.close(wfd)
                workers.append([pid, rfd, True])

        fallback_buf = None
        for idx, path in enumerate(files):
            w = idx % n_workers
            entry = workers[w]
            if entry[2]:
                rfd = entry[1]
                hdr = None
                try:
                    hdr = _read_exact(rfd, _FRAME_HDR.size)
                except OSError:
                    hdr = None
                if hdr is not None:
                    got_idx, plen = _FRAME_HDR.unpack(hdr)
                    if got_idx == idx:
                        if plen == 0:
                            yield idx, 0, ()
                            continue
                        try:
                            raw_payload = _read_exact(rfd, plen)
                            if raw_payload is not None:
                                raw_count, recs = marshal.loads(raw_payload)
                                yield idx, raw_count, recs
                                continue
                        except Exception:
                            pass
                # Worker died or sent malformed frame: close its pipe and rescan remaining files locally.
                entry[2] = False
                if rfd is not None:
                    try:
                        os.close(rfd)
                    except OSError:
                        pass
                    entry[1] = None

            if fallback_buf is None:
                fallback_buf = bytearray(_CHUNK_SIZE)
            raw_count, recs = _scan_one_file(path, fallback_buf)
            yield idx, raw_count, recs
    finally:
        for entry in workers:
            pid, rfd, _ = entry
            if rfd is not None:
                try:
                    os.close(rfd)
                except OSError:
                    pass
                entry[1] = None
            if pid is not None:
                # Reap naturally first; escalate to SIGTERM if parent aborted early.
                try:
                    wpid, _ = os.waitpid(pid, os.WNOHANG)
                    if wpid == 0:
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        os.waitpid(pid, 0)
                except ChildProcessError:
                    pass
                entry[0] = None


def iter_errors(path):
    for date_s, txt, _ in _iter_error_records(path):
        yield date_s, txt


def _load_seen_with_tids(ledger):
    seen = set()
    seen_tids = set()
    rows = 0
    if ledger.exists():
        with open(ledger, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = _JSON_DECODE(line)
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue
                sig = d.get("sig")
                session = d.get("session")
                if isinstance(sig, str) and isinstance(session, str):
                    seen.add((sig, session))
                tid = d.get("tool_use_id")
                if isinstance(tid, str) and tid:
                    seen_tids.add(tid)
                rows += 1
    return seen, seen_tids, rows


def load_seen(ledger):
    seen, _, rows = _load_seen_with_tids(ledger)
    return seen, rows


def _session_stem(path_str):
    name = os.path.basename(path_str)
    return name[:-6] if len(name) > 6 else name


def _discover_jsonl_files(root, cutoff):
    return discover_session_jsonl_files(root, cutoff)


def main(argv=None, *, record_iter=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.days is not None and args.days < 1:
        print("Error: --days must be >= 1", file=sys.stderr)
        return 2

    if not PROJECTS.is_dir():
        print(f"Error: projects directory does not exist: {PROJECTS}", file=sys.stderr)
        return 2

    started = time.time()
    cutoff = None
    if args.rebuild:
        days = args.days if args.days is not None else 180
        cutoff = started - days * 86400
    elif args.days is not None:
        cutoff = started - args.days * 86400
    elif STATE.exists():
        cutoff = STATE.stat().st_mtime

    files = _discover_jsonl_files(PROJECTS, cutoff)

    n_workers = min(os.cpu_count() or 1, _MAX_WORKERS, max(1, len(files) // 8))
    use_fork = record_iter is None and hasattr(os, "fork") and len(files) >= _FORK_MIN_FILES and n_workers > 1

    if args.rebuild:
        seen, seen_tids, prior = set(), set(), 0
    else:
        seen, seen_tids, prior = _load_seen_with_tids(LEDGER)

    scanned = len(files)
    raw_errors = 0
    new_rows = 0
    fallback_date = time.strftime("%Y-%m-%d", time.gmtime(started))
    dumps = json.dumps

    def _emit_deduped(emit_line):
        nonlocal raw_errors, new_rows
        if use_fork:
            for idx, raw_count, recs in _iter_files_parallel(files, n_workers):
                if not raw_count:
                    continue
                raw_errors += raw_count
                session = _session_stem(files[idx])
                for date, sig, tuid in recs:
                    if tuid and tuid in seen_tids:
                        continue
                    key = (sig, session)
                    if key in seen:
                        continue
                    seen.add(key)
                    if tuid:
                        seen_tids.add(tuid)
                    new_rows += 1
                    if emit_line is not None:
                        row = {"date": date or fallback_date, "sig": sig, "session": session}
                        if tuid:
                            row["tool_use_id"] = tuid
                        emit_line(dumps(row, ensure_ascii=False) + "\n")
        else:
            rec_fn = record_iter if record_iter is not None else _iter_error_records
            for p in files:
                session = _session_stem(p)
                for date, txt, tuid in rec_fn(Path(p)):
                    raw_errors += 1
                    sig = normalize(txt)
                    if len(sig) < MIN_SIG_LEN:
                        continue
                    if tuid and tuid in seen_tids:
                        continue
                    key = (sig, session)
                    if key in seen:
                        continue
                    seen.add(key)
                    if tuid:
                        seen_tids.add(tuid)
                    new_rows += 1
                    if emit_line is not None:
                        row = {"date": date or fallback_date, "sig": sig, "session": session}
                        if tuid:
                            row["tool_use_id"] = tuid
                        emit_line(dumps(row, ensure_ascii=False) + "\n")

    if args.dry_run:
        _emit_deduped(None)
        print(
            f"DRY-RUN scanned={scanned} raw_errors={raw_errors} new_rows={new_rows} "
            f"prior_rows={prior} elapsed={time.time() - started:.1f}s"
        )
        return 0

    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    if args.rebuild:
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=LEDGER.parent,
                prefix=LEDGER.name + ".",
                suffix=".tmp",
                delete=False,
            ) as f:
                tmp_path = f.name
                write_buf = []

                def _write_line(line):
                    write_buf.append(line)
                    if len(write_buf) >= 512:
                        f.write("".join(write_buf))
                        write_buf.clear()

                _emit_deduped(_write_line)
                if write_buf:
                    f.write("".join(write_buf))
            os.replace(tmp_path, LEDGER)
            tmp_path = None
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        total = new_rows
    else:
        out_lines = []
        _emit_deduped(out_lines.append)
        need_leading_nl = False
        if out_lines and LEDGER.exists():
            try:
                if LEDGER.stat().st_size > 0:
                    with open(LEDGER, "rb") as rf:
                        rf.seek(-1, os.SEEK_END)
                        need_leading_nl = rf.read(1) != b"\n"
            except OSError:
                pass
        with open(LEDGER, "a", encoding="utf-8") as f:
            if need_leading_nl:
                f.write("\n")
            if out_lines:
                f.write("".join(out_lines))
        total = prior + new_rows

    # 只在全量重建或常規增量模式更新游標；使用掃描起點而非結束時刻，避免掃描期間追加的錯誤下輪漏計
    if args.rebuild or args.days is None:
        STATE.touch()
        cursor_ts = math.floor(started)
        os.utime(STATE, (cursor_ts, cursor_ts))
    print(f"scanned={scanned} ledger_total={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
