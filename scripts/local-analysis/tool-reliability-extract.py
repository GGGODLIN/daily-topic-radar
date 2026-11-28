#!/usr/bin/env python3
"""tool-reliability-extract.py — per-tool 可靠度統計（2026-08-01 建，HKUDS/OpenSpace 抽件 2）。

問題：既有 recurring-errors ledger 記的是「錯誤簽名」，不知道錯誤是哪個工具吐的。
所以答不出「哪個工具最容易掛」「這週有沒有變差」。

做法：唯讀掃 session jsonl，用 tool_use_id 把 assistant 的 tool_use（有工具名）
跟 user 的 tool_result（有 is_error）配對，按 ISO 週 × 工具聚合。實測配對率 100%。

**既有 recurring-errors-ledger.jsonl 零改動**——那支管錯誤簽名去重，這支管工具歸因，
兩件事分開。本檔的輸出 100% 是衍生資料，砍掉重算隨時可以。

設計取捨（拍板紀錄）：
- **不收 latency**（單次呼叫延遲另由 `tool-latency` 統計）。2026-08-01 建檔時曾觀察同一則
  assistant 訊息內並行發出的工具共用一個 timestamp；2026-11-10 實測確認 CC 每個區塊各有時間戳，
  但同批工具耗時仍受序列／批次排程影響，故本檔只負責錯誤率歸因，延遲交由 `tool-latency` 處理。
- **不設警報門檻**。第一次量，沒人知道 Agent 12.8% 算高還是正常。先讓數字可得、累積幾週
  有體感再定閾值——閾值拍歪的代價（誤報讓人以後忽略這個 channel）比沒閾值更糟。
- **趨勢看週對週**，不是絕對值。這是使用者 2026-08-01 拍板：「第一次就先報，之後長期趨勢
  穩定的話就不用報，也就是還是看趨勢的」。

**永遠全量重算、沒有增量模式**——這是刻意的。session jsonl 會持續被追加，mtime 跟著更新，
增量會把同一個檔的既有計數再加一次。全掃 180 天約 10 秒，不值得為此背一個雙重計數的 bug。
輸出是純衍生資料，砍掉重算隨時可以。

用法：
  python3 tool-reliability-extract.py                # 全量重算（預設回看 180 天）
  python3 tool-reliability-extract.py --report       # 印人讀週報（給 weekly channel 用）
  --days N（改回看天數）/ --dry-run

輸出：~/code/social-info/reports/local-analysis/tool-reliability.jsonl
每行 {week, tool, calls, errors, rate}，week 為 ISO 年-週（2026-W31）。
"""

import argparse
import datetime as dt
import json
import os
import pickle
import signal
import sys
import tempfile
import time
from collections import defaultdict
from contextlib import ExitStack
from fractions import Fraction
from pathlib import Path

# Isolated mode (-I) leaves the script directory off sys.path.
if sys.flags.safe_path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from session_jsonl import discover_session_jsonl_files, parse_iso_utc as _parse_ts_dt

HOME = Path.home()
PROJECTS = Path(os.environ.get("TOOL_RELIABILITY_PROJECTS_DIR") or (HOME / ".claude" / "projects"))
OUT = Path(
    os.environ.get("TOOL_RELIABILITY_DATA_FILE")
    or (HOME / "code" / "social-info" / "reports" / "local-analysis" / "tool-reliability.jsonl")
)

MIN_CALLS_FOR_RATE = 20
DELTA_ALERT = Fraction(3, 100)
HIGH_RATE = 0.15
TOP_NOTABLE = 5

_JSON_SCAN_ONCE = json.JSONDecoder().scan_once
_STRIP_WS = b" \t\r\n"
_TOOL_MARKERS = (b'"tool_use"', b'"tool_result"')
_VALID_24 = frozenset(f"{i:02d}" for i in range(24))
_VALID_60 = frozenset(f"{i:02d}" for i in range(60))
_PY311_PLUS = sys.version_info >= (3, 11)
_UTC_DATE_CACHE = {}
_YMD_WEEK_CACHE = {}


def _utc_date_info(date_key):
    info = _UTC_DATE_CACHE.get(date_key)
    if info is not None:
        return info
    if (
        len(date_key) == 10
        and date_key[4] == "-"
        and date_key[7] == "-"
        and date_key[:4].isascii()
        and date_key[:4].isdigit()
        and date_key[5:7].isascii()
        and date_key[5:7].isdigit()
        and date_key[8:10].isascii()
        and date_key[8:10].isdigit()
    ):
        try:
            d0 = dt.datetime(
                int(date_key[:4]),
                int(date_key[5:7]),
                int(date_key[8:10]),
                tzinfo=dt.timezone.utc,
            )
        except (ValueError, OverflowError):
            return None
        y, w, _ = d0.isocalendar()
        info = (d0.timestamp(), f"{y:04d}-W{w:02d}")
        if len(_UTC_DATE_CACHE) < 4096:
            _UTC_DATE_CACHE[date_key] = info
        return info
    return None


def _week_for_dt(d):
    key = (d.year, d.month, d.day)
    wk = _YMD_WEEK_CACHE.get(key)
    if wk is None:
        y, w, _ = d.isocalendar()
        wk = f"{y:04d}-W{w:02d}"
        if len(_YMD_WEEK_CACHE) < 4096:
            _YMD_WEEK_CACHE[key] = wk
    return wk


def _ts_to_week(ts, cutoff=None):
    if not isinstance(ts, str):
        return None
    # Python <3.11 fromisoformat rejects non-3/6-digit fractional seconds; gate fast path to >=3.11.
    if _PY311_PLUS:
        n = len(ts)
        if (
            n >= 20
            and ts[10] == "T"
            and (ts[-1] == "Z" or ts[-1] == "z")
            and ts[13] == ":"
            and ts[16] == ":"
            and ts[11:13] in _VALID_24
            and ts[14:16] in _VALID_60
            and ts[17:19] in _VALID_60
            and (
                n == 20
                or (
                    ts[19] == "."
                    and 21 < n <= 29
                    and ts[20:-1].isascii()
                    and ts[20:-1].isdigit()
                )
            )
        ):
            info = _utc_date_info(ts[:10])
            if info is not None:
                midnight_ts, wk = info
                if cutoff is None or midnight_ts >= cutoff:
                    return wk
                if midnight_ts + 86400.0 <= cutoff:
                    return None
    d_ts = _parse_ts_dt(ts)
    if d_ts is None:
        return None
    if cutoff is not None:
        ev_dt = d_ts if d_ts.tzinfo is not None else d_ts.replace(tzinfo=dt.timezone.utc)
        if ev_dt.timestamp() < cutoff:
            return None
    return _week_for_dt(d_ts)


def iso_week(ts):
    return _ts_to_week(ts)


def _extract_from_line(b_line, names, pairs, scan_once=_JSON_SCAN_ONCE):
    if not b_line:
        return
    if b_line[0] != 123:
        b_line = b_line.strip(_STRIP_WS)
        if not b_line or b_line[0] != 123:
            return
    if b'"message"' not in b_line and b"\\u" not in b_line:
        return
    if b'"content"' not in b_line and b"\\u" not in b_line:
        return
    s = b_line.decode("utf-8", "replace")
    try:
        d, end = scan_once(s, 0)
    except Exception:
        return
    if (end != len(s) and s[end:].strip(" \t\r\n")) or type(d) is not dict:
        return
    msg = d.get("message")
    if type(msg) is not dict:
        return
    content = msg.get("content")
    if type(content) is not list:
        return
    dtype = d.get("type")
    if dtype == "assistant":
        for b in content:
            if type(b) is dict and b.get("type") == "tool_use":
                tid = b.get("id")
                name = b.get("name")
                if type(tid) is str and tid and type(name) is str and name:
                    names.setdefault(tid, name)
    elif dtype == "user":
        ts = d.get("timestamp")
        for b in content:
            if type(b) is dict and b.get("type") == "tool_result":
                tuid = b.get("tool_use_id")
                if type(tuid) is str and tuid:
                    pairs.append((tuid, b.get("is_error") is True, ts))


def _file_pairs(path, cutoff):
    names = {}
    pairs = []
    scan_once = _JSON_SCAN_ONCE
    extract = _extract_from_line
    try:
        with open(path, "rb", buffering=262144) as f:
            for raw_line in f:
                idx = raw_line.find(b'"tool_')
                if idx == -1:
                    continue
                if not raw_line.startswith(_TOOL_MARKERS, idx):
                    if b'"tool_use"' not in raw_line and b'"tool_result"' not in raw_line:
                        continue
                cr = raw_line.find(b"\r")
                if cr != -1 and not (cr == len(raw_line) - 2 and raw_line[-1] == 10):
                    for sub in raw_line.splitlines():
                        if b'"tool_use"' in sub or b'"tool_result"' in sub:
                            extract(sub, names, pairs, scan_once)
                else:
                    extract(raw_line, names, pairs, scan_once)
    except OSError:
        return
    ts_to_week = _ts_to_week
    for tid, err, ts in pairs:
        name = names.get(tid)
        if not name:
            continue
        wk = ts_to_week(ts, cutoff)
        if wk is None:
            continue
        yield tid, (wk, name, err)


def _unique_pairs(pairs, seen):
    for tid, row in pairs:
        if tid not in seen:
            seen.add(tid)
            yield row


def scan_file(path, seen_tids=None, cutoff=None):
    seen = seen_tids if seen_tids is not None else set()
    return list(_unique_pairs(_file_pairs(path, cutoff), seen))


def _scan_files(paths, cutoff):
    for path in paths:
        yield from _file_pairs(path, cutoff)


def _scan_parallel(paths, cutoff):
    cpu_count = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    workers = min(4, cpu_count or 1)
    if not hasattr(os, "fork") or workers < 2 or len(paths) < 4 * workers:
        yield from _scan_files(paths, cutoff)
        return

    # Consecutive slices preserve Path ordering when we replay each batch.
    # Workers write to private files so later batches finish without IPC stalls.
    cuts = [i * len(paths) // workers for i in range(workers + 1)]
    children = {}
    fork_failed = False
    with ExitStack() as stack:
        try:
            for start, end in zip(cuts[1:-1], cuts[2:]):
                spool = stack.enter_context(tempfile.TemporaryFile())
                try:
                    pid = os.fork()
                except OSError:
                    fork_failed = True
                    break
                if pid == 0:
                    rc = 1
                    try:
                        for path in paths[start:end]:
                            pickle.dump(list(_file_pairs(path, cutoff)), spool, pickle.HIGHEST_PROTOCOL)
                        spool.flush()
                        rc = 0
                    except BaseException:
                        rc = 1
                    finally:
                        os._exit(rc)
                children[pid] = (spool, end - start)

            if fork_failed:
                for pid, (_, count) in list(children.items()):
                    if count:
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        os.waitpid(pid, 0)
                        children[pid] = (None, 0)
                yield from _scan_files(paths, cutoff)
                return

            yield from _scan_files(paths[: cuts[1]], cutoff)
            for pid, (spool, count) in children.items():
                _, status = os.waitpid(pid, 0)
                children[pid] = (spool, 0)
                if status != 0:
                    raise RuntimeError(f"tool reliability scan worker {pid} failed ({status})")
                spool.seek(0)
                for _ in range(count):
                    yield from pickle.load(spool)
        finally:
            for pid, (_, count) in children.items():
                if count:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        os.waitpid(pid, 0)
                    except ChildProcessError:
                        pass


def write_agg(agg):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=OUT.parent,
            prefix=OUT.name + ".",
            suffix=".tmp",
            delete=False,
        ) as f:
            tmp_path = f.name
            for (wk, tool), (calls, errors) in sorted(agg.items()):
                rate = round(errors / calls, 4) if calls else 0.0
                f.write(
                    json.dumps(
                        {"week": wk, "tool": tool, "calls": calls, "errors": errors, "rate": rate},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        os.replace(tmp_path, OUT)
        tmp_path = None
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def report():
    rows = defaultdict(lambda: [0, 0])
    if not OUT.exists():
        print("尚無資料，先跑一次 tool-reliability-extract.py", file=sys.stderr)
        return 1
    with open(OUT, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            wk = d.get("week")
            tool = d.get("tool")
            calls = d.get("calls")
            errors = d.get("errors")
            if (
                not isinstance(wk, str)
                or not wk
                or not isinstance(tool, str)
                or not tool
                or not isinstance(calls, int)
                or isinstance(calls, bool)
                or calls < 0
                or not isinstance(errors, int)
                or isinstance(errors, bool)
                or errors < 0
            ):
                continue
            rows[(wk, tool)] = [calls, errors]
    weeks = sorted({k[0] for k in rows})
    if not weeks:
        print("尚無資料", file=sys.stderr)
        return 1
    cur = weeks[-1]
    prev = weeks[-2] if len(weeks) > 1 else None

    sized = []
    small_n = small_calls = 0
    for (w, t), (calls, errors) in rows.items():
        if w != cur:
            continue
        if calls < MIN_CALLS_FOR_RATE:
            small_n += 1
            small_calls += calls
            continue
        rate = Fraction(errors, calls)
        pv = rows.get((prev, t)) if prev else None
        prate = Fraction(pv[1], pv[0]) if pv and pv[0] >= MIN_CALLS_FOR_RATE else None
        sized.append((t, calls, errors, rate, prate))

    print(f"## 工具可靠度：{cur}" + (f"（對照 {prev}）" if prev else "（無前一週可比）"))
    print()

    notable = []
    for t, calls, errors, rate, prate in sized:
        if prate is not None and abs(rate - prate) >= DELTA_ALERT:
            notable.append((abs(rate - prate), t, calls, errors, rate, prate))
        elif prate is None and float(rate) >= HIGH_RATE:
            notable.append((rate, t, calls, errors, rate, None))
    notable.sort(reverse=True)

    if notable:
        print(f"**值得看（錯誤率變動 ≥{float(DELTA_ALERT):.0%} 或新工具錯誤率 ≥{HIGH_RATE:.0%}）**")
        print()
        for _, t, calls, errors, rate, prate in notable[:TOP_NOTABLE]:
            if prate is None:
                print(f"- `{t}` {float(rate):.0%}（{errors}/{calls}）— 無前週基期")
            else:
                delta = float(rate - prate)
                print(
                    f"- `{t}` {float(prate):.0%} → {float(rate):.0%}（{delta:+.0%}，{errors}/{calls}）"
                )
        print()
    else:
        print("**值得看**：無——所有工具錯誤率與前週差異均在門檻內")
        print()

    print("<details><summary>全部工具（本週呼叫 ≥ %d）</summary>" % MIN_CALLS_FOR_RATE)
    print()
    print("| 工具 | 呼叫 | 錯誤 | 錯誤率 | 前週 |")
    print("|---|---|---|---|---|")
    for t, calls, errors, rate, prate in sorted(sized, key=lambda x: -x[1]):
        pr = f"{float(prate):.1%}" if prate is not None else "—"
        print(f"| {t} | {calls} | {errors} | {float(rate):.1%} | {pr} |")
    print()
    print("</details>")
    print()
    print(
        f"（本週另有 {small_n} 個工具呼叫數 < {MIN_CALLS_FOR_RATE}、合計 {small_calls} 次，"
        f"分母太小不計比率；歷史週數 {len(weeks)}：{weeks[0]} → {weeks[-1]}）"
    )
    return 0


def _collect_jsonl_files(root, cutoff):
    return discover_session_jsonl_files(str(root), cutoff, check_dir_first=True)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.report:
        return report()

    if args.days is not None and args.days < 1:
        print("Error: --days must be >= 1", file=sys.stderr)
        return 2

    if not PROJECTS.is_dir():
        print(f"Error: projects directory does not exist: {PROJECTS}", file=sys.stderr)
        return 2

    started = time.time()
    days = args.days if args.days is not None else 180
    cutoff = started - days * 86400

    files = _collect_jsonl_files(PROJECTS, cutoff)

    agg = defaultdict(lambda: [0, 0])
    seen_tids = set()
    scanned = len(files)
    added = 0
    for wk, tool, err in _unique_pairs(_scan_parallel(files, cutoff), seen_tids):
        agg[(wk, tool)][0] += 1
        added += 1
        if err:
            agg[(wk, tool)][1] += 1

    if args.dry_run:
        print(
            f"DRY-RUN scanned={scanned} pairs={added} groups={len(agg)} "
            f"elapsed={time.time() - started:.1f}s"
        )
        return 0

    write_agg(agg)
    print(f"scanned={scanned} pairs={added} groups={len(agg)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
