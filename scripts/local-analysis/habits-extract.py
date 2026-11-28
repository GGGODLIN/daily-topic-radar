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
import errno
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

HOME = Path.home()
DEFAULT_OUT_DIR = HOME / "code" / "social-info" / "reports" / "local-analysis" / "habits"
WINDOW_DAYS = 21
SEGMENT_DAYS = 7
SHORT_MAX_CHARS = 6
MAX_MESSAGE_CHARS = 2000
TOP_SHORT = 30
MAX_PART_BYTES = 48_000
MAX_PARTS_PER_SLICE = 20
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


# Restrict to ASCII digits so full-width Unicode digits do not bypass fromisoformat rejection.
_ISO_UTC_Z_RE = re.compile(
    r"^([0-9]{4}-[0-9]{2}-[0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?Z$"
)


def parse_iso_timestamp(ts):
    """解析 ISO 時間戳；回傳 (sort_key, utc_or_naive_date) 或非法時回 None。"""
    if not isinstance(ts, str):
        return None
    raw = ts.strip()
    if len(raw) < 10:
        return None
    m = _ISO_UTC_Z_RE.match(raw)
    if m is not None:
        date_s, hh, mm, ss, frac = m.groups()
        try:
            dt.date.fromisoformat(date_s)
        except ValueError:
            return None
        if int(hh) > 23 or int(mm) > 59 or int(ss) > 59:
            return None
        if frac is None:
            return (f"{date_s}T{hh}:{mm}:{ss}", date_s)
        micro = frac.ljust(6, "0")
        if micro == "000000":
            return (f"{date_s}T{hh}:{mm}:{ss}", date_s)
        return (f"{date_s}T{hh}:{mm}:{ss}.{micro}", date_s)
    if not DATE_RE.match(raw[:10]):
        return None
    try:
        dt.date.fromisoformat(raw[:10])
        if len(raw) == 10:
            parsed = dt.datetime.fromisoformat(raw)
        else:
            parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        utc = parsed.astimezone(dt.timezone.utc)
        return (utc.replace(tzinfo=None).isoformat(), utc.date().isoformat())
    return (parsed.isoformat(), parsed.date().isoformat())


def parse_user_message(event):
    """回傳 (ts, session, project, text)；不是使用者本人打的訊息回 None。"""
    if not isinstance(event, dict):
        return None
    if event.get("role") != "user":
        return None
    path = event.get("path")
    if not isinstance(path, str) or "/subagents/" in path or PROJECT_MARK not in path:
        return None
    raw_text = event.get("text")
    if not isinstance(raw_text, str):
        return None
    text = raw_text.strip()
    if not text or text.startswith(SKIP_PREFIXES) or len(text) > MAX_MESSAGE_CHARS:
        return None
    if "task-notification" in text or "system-reminder" in text:
        return None
    raw_ts = event.get("ts")
    if raw_ts is not None and not isinstance(raw_ts, str):
        return None
    raw_session = event.get("session")
    if raw_session is not None and not isinstance(raw_session, str):
        return None
    if "\n" not in path:
        project = path.rsplit(PROJECT_MARK, 1)[1].split("/", 1)[0]
    else:
        project = re.sub(r".*/projects/-Users-linhancheng-", "", path).split("/")[0]
    return (raw_ts or "", raw_session or "", project, text)


def _collect_messages_with_dates(events):
    seen = set()
    entries = []
    for event in events:
        parsed = parse_user_message(event)
        if parsed is None:
            continue
        ts, session, project, text = parsed
        key = (session, ts, text[:80])
        if key in seen:
            continue
        seen.add(key)
        parsed_ts = parse_iso_timestamp(ts)
        sort_ts = parsed_ts[0] if parsed_ts is not None else ts
        utc_date = parsed_ts[1] if parsed_ts is not None else None
        entries.append(((sort_ts, ts, session, project, text), utc_date, parsed))
    entries.sort(key=lambda item: item[0])
    return entries


def collect_messages(events):
    return [item[2] for item in _collect_messages_with_dates(events)]


def segment_bounds(date):
    """回傳三段 (start, end)，皆含頭尾；最後一段的 end 是 date 當天。"""
    if not isinstance(date, str) or not DATE_RE.match(date):
        raise ValueError(f"date must be YYYY-MM-DD: {date!r}")
    end = dt.date.fromisoformat(date)
    first = end - dt.timedelta(days=WINDOW_DAYS - 1)
    bounds = []
    for i in range(WINDOW_DAYS // SEGMENT_DAYS):
        start = first + dt.timedelta(days=i * SEGMENT_DAYS)
        bounds.append((start.isoformat(), (start + dt.timedelta(days=SEGMENT_DAYS - 1)).isoformat()))
    return bounds


def in_segment(message, start, end):
    parsed = parse_iso_timestamp(message[0])
    if parsed is None:
        return False
    return start <= parsed[1] <= end


def format_body_line(message):
    ts, session, project, text = message
    return f"{ts[:16]} | {session[:8]} | {project} | {text.replace(chr(10), ' ⏎ ')}"


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
    for m in body:
        lines.append(format_body_line(m))
    return "\n".join(lines) + "\n", len(body), len(sessions)


def split_slice_parts(index, start, end, messages, max_part_bytes=MAX_PART_BYTES):
    """當單段全文超過 max_part_bytes 時，將正文拆成 <= max_part_bytes 的多個子批文字。"""
    short = collections.Counter(m[3] for m in messages if len(m[3]) <= SHORT_MAX_CHARS)
    body = [m for m in messages if len(m[3]) > SHORT_MAX_CHARS]
    sessions = {m[1] for m in messages}
    top = "；".join(f"{text}×{n}" for text, n in short.most_common(TOP_SHORT))
    body_lines = [format_body_line(m) + "\n" for m in body]
    line_sizes = [len(line.encode("utf-8")) for line in body_lines]

    def header(part_no, total_parts, part_len):
        lines = [
            f"# 第 {index} 段第 {part_no}/{total_parts} 批：{start} ~ {end}，"
            f"本批 {part_len} 則正文訊息（全段共 {len(messages)} 則使用者訊息、{len(sessions)} 個 session）",
        ]
        if part_no == 1:
            lines.extend([
                f"## 短回覆次數（全段 ≤{SHORT_MAX_CHARS} 字，前 {TOP_SHORT}，僅列於第 1 批）",
                top,
                "",
            ])
        lines.append("## 訊息全文（時間｜session｜專案｜內容）")
        return "\n".join(lines) + "\n"

    # 批號及總批數的位數會改變檔頭大小，需依實際批數檢查位元組。
    for total_parts in range(1, min(MAX_PARTS_PER_SLICE, len(body_lines)) + 1):
        spans = []
        first = 0
        for part_no in range(1, total_parts + 1):
            last = first
            used = 0
            header_base = len(header(part_no, total_parts, 0).encode("utf-8")) - 1
            while last < len(body_lines):
                next_size = used + line_sizes[last]
                if header_base + len(str(last - first + 1)) + next_size > max_part_bytes:
                    break
                used = next_size
                last += 1
            if last == first:
                break
            spans.append((first, last))
            first = last
        if first == len(body_lines) and len(spans) == total_parts:
            return [
                (part_no, header(part_no, total_parts, last - first) + "".join(body_lines[first:last]), last - first)
                for part_no, (first, last) in enumerate(spans, start=1)
            ]
    return []


def write_private(path, text):
    # 明確拒絕 symlink 目標，避免將私人訊息寫到別處或覆蓋符號連結。
    if os.path.islink(path):
        raise OSError(errno.ELOOP, "refusing symlink target", str(path))
    directory = os.path.dirname(os.fspath(path)) or "."
    prefix = f".{os.path.basename(os.fspath(path))}."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if os.path.islink(path):
            raise OSError(errno.ELOOP, "refusing symlink target", str(path))
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


class _CcsiEventsError(RuntimeError):
    def __init__(self, returncode, stderr, yielded):
        super().__init__(f"ccsi events exit {returncode}: {stderr.strip()[:300]}")
        self.returncode = returncode
        self.stderr = stderr
        self.yielded = yielded


def _run_ccsi_events(cmd):
    loads = json.loads
    # Route stderr to a TemporaryFile instead of PIPE so >64 KiB warnings cannot deadlock stdout.
    with tempfile.TemporaryFile(mode="w+b") as err_file:
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err_file) as proc:
            yielded = False
            for raw_line in proc.stdout:
                yielded = True
                try:
                    yield loads(raw_line)
                except UnicodeDecodeError:
                    try:
                        yield loads(raw_line.decode("utf-8", errors="replace"))
                    except ValueError:
                        continue
                except ValueError:
                    continue
            rc = proc.wait()
            if rc != 0:
                err_file.seek(0)
                stderr = err_file.read(4096).decode("utf-8", errors="replace")
                raise _CcsiEventsError(rc, stderr, yielded)


def ccsi_events(since):
    binary = shutil.which("ccsi") or str(HOME / ".local" / "bin" / "ccsi")
    base_cmd = [binary, "events", "--vendor", "cc", "--since", since, "--kind", "text"]
    filtered_cmd = [*base_cmd, "--role", "user", "--exclude-subagents"]
    try:
        yield from _run_ccsi_events(filtered_cmd)
    except _CcsiEventsError as err:
        # 舊版 ccsi 不支援 --role / --exclude-subagents 時自動回退至相容旗標。
        if err.returncode == 2 and not err.yielded and "unrecognized arguments" in err.stderr:
            yield from _run_ccsi_events(base_cmd)
        else:
            raise


def file_events(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=dt.date.today().isoformat())
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--max-part-bytes", type=int, default=MAX_PART_BYTES)
    # 測試用：讀 ndjson 檔取代 ccsi，讓切段與過濾能用固定資料驗。
    parser.add_argument("--events-file", default=None)
    args = parser.parse_args(argv)
    if args.max_part_bytes < 4_000:
        print(f"--max-part-bytes 必須 >= 4000：{args.max_part_bytes}", file=sys.stderr)
        return 2

    try:
        bounds = segment_bounds(args.date)
    except (ValueError, OverflowError):
        print(f"--date 必須是可回看 {WINDOW_DAYS} 天的 YYYY-MM-DD：{args.date}", file=sys.stderr)
        return 2

    try:
        events = file_events(args.events_file) if args.events_file else ccsi_events(bounds[0][0])
        entries = _collect_messages_with_dates(events)
    except (OSError, RuntimeError) as err:
        print(f"抽取失敗：{err}", file=sys.stderr)
        return 2

    per_slice = [[] for _ in bounds]
    for _sort_key, utc_date, msg in entries:
        if utc_date is None:
            continue
        for idx, (s, e) in enumerate(bounds):
            if s <= utc_date <= e:
                per_slice[idx].append(msg)
                break
    if not any(per_slice):
        print("三段合計 0 則訊息：疑似 ccsi 索引或過濾條件壞了，不寫檔", file=sys.stderr)
        return 3

    out_dir = Path(args.out_dir)
    if out_dir.is_symlink():
        raise OSError(errno.ELOOP, "refusing symlink out_dir", str(out_dir))
    if out_dir.exists() and not out_dir.is_dir():
        raise OSError(errno.ENOTDIR, "out_dir is not a directory", str(out_dir))
    planned_writes = []
    planned_paths = set()
    slices = []
    for i, ((start, end), msgs) in enumerate(zip(bounds, per_slice), start=1):
        text, body_count, session_count = render_slice(i, start, end, msgs)
        path = out_dir / f"{args.date}-habits-slice-{i}.txt"
        planned_writes.append((path, text))
        planned_paths.add(path)
        slice_entry = {
            "index": i,
            "start": start,
            "end": end,
            "path": str(path),
            "total_messages": len(msgs),
            "body_messages": body_count,
            "sessions": session_count,
        }
        if len(text.encode("utf-8")) > args.max_part_bytes:
            parts = split_slice_parts(i, start, end, msgs, max_part_bytes=args.max_part_bytes)
            if not parts:
                print(f"第 {i} 段無法切成 {MAX_PARTS_PER_SLICE} 批以內且符合 --max-part-bytes", file=sys.stderr)
                return 2
            parts_meta = []
            for part_no, part_text, part_body_count in parts:
                part_path = out_dir / f"{args.date}-habits-slice-{i}-part-{part_no}.txt"
                planned_writes.append((part_path, part_text))
                planned_paths.add(part_path)
                parts_meta.append({
                    "part": part_no,
                    "path": str(part_path),
                    "body_messages": part_body_count,
                })
            slice_entry["parts"] = parts_meta
        slices.append(slice_entry)

    # 預檢所有目標路徑與同日既有 part 檔，任一為 symlink 就於零寫入前拒寫。
    stale_parts = []
    for i in range(1, len(bounds) + 1):
        for existing in out_dir.glob(f"{args.date}-habits-slice-{i}-part-*.txt"):
            if existing.is_symlink():
                raise OSError(errno.ELOOP, "refusing symlink part file", str(existing))
            if existing not in planned_paths:
                stale_parts.append(existing)
    for target_path, _ in planned_writes:
        if target_path.is_symlink():
            raise OSError(errno.ELOOP, "refusing symlink target", str(target_path))

    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    for target_path, content in planned_writes:
        write_private(target_path, content)
    for stale in stale_parts:
        try:
            stale.unlink()
        except FileNotFoundError:
            pass
    print(json.dumps({"date": args.date, "since": bounds[0][0], "slices": slices}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
