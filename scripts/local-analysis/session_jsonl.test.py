#!/usr/bin/env python3
"""session_jsonl.py 的單元與契約回歸測試。

跑法：python3 session_jsonl.test.py
"""

import datetime as dt
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import session_jsonl


def _load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


trx = _load_script("trx_test", "tool-reliability-extract.py")
rec = _load_script("rec_test", "recurring-errors-extract.py")

PASS = FAIL = 0


def check(name, want, got):
    global PASS, FAIL
    if want == got:
        print(f"  PASS  {name}")
        PASS += 1
    else:
        print(f"  FAIL  {name}\n        want {want!r}\n        got  {got!r}")
        FAIL += 1


print("\n=== parse_iso_utc：有效與邊界輸入 ===")
check(
    "UTC Z 結尾正規化為 UTC aware",
    dt.datetime(2026, 7, 28, 10, 0, 0, tzinfo=dt.timezone.utc),
    session_jsonl.parse_iso_utc("2026-07-28T10:00:00Z"),
)
check(
    "小寫 z 與前後空白正常解析",
    dt.datetime(2026, 7, 28, 10, 0, 0, 123000, tzinfo=dt.timezone.utc),
    session_jsonl.parse_iso_utc("  2026-07-28T10:00:00.123z\n"),
)
check(
    "帶時區偏移跨日轉為 UTC",
    dt.datetime(2026, 8, 2, 20, 0, 0, tzinfo=dt.timezone.utc),
    session_jsonl.parse_iso_utc("2026-08-03T04:00:00+08:00"),
)
check(
    "帶負偏移跨年轉為 UTC",
    dt.datetime(2027, 1, 1, 2, 30, 0, tzinfo=dt.timezone.utc),
    session_jsonl.parse_iso_utc("2026-12-31T21:30:00-05:00"),
)
naive = session_jsonl.parse_iso_utc("2026-07-28T10:00:00")
check("naive 時間戳保持 naive", (dt.datetime(2026, 7, 28, 10, 0, 0), None), (naive, naive.tzinfo))
date_only = session_jsonl.parse_iso_utc("2026-07-28")
check("純日期保持 naive 午夜", (dt.datetime(2026, 7, 28, 0, 0, 0), None), (date_only, date_only.tzinfo))

for label, bad in [
    ("None", None),
    ("bool", True),
    ("int", 1785614400),
    ("float", 1785614400.5),
    ("bytes", b"2026-07-28T10:00:00Z"),
    ("空字串", ""),
    ("全空白", "   \t\n"),
    ("無效格式", "not-a-date"),
    ("不存在日期", "2026-02-30T00:00:00Z"),
    ("雙重時區", "2026-07-28T10:00:00+00:00Z"),
    ("年下溢 OverflowError", "0001-01-01T00:00:00+08:00"),
    ("年上溢 OverflowError", "9999-12-31T23:59:59-08:00"),
]:
    check(f"parse_iso_utc 拒絕 {label}", None, session_jsonl.parse_iso_utc(bad))
    check(f"_parse_ts_dt 拒絕 {label}", None, trx._parse_ts_dt(bad))
    check(f"_extract_date 拒絕 {label}", "", rec._extract_date(bad))

check("_parse_ts_dt 回傳 datetime", dt.datetime(2026, 3, 15, 17, 0, tzinfo=dt.timezone.utc), trx._parse_ts_dt("2026-03-16T01:00:00+08:00"))
check("_extract_date 回傳 UTC 日期字串", "2026-03-15", rec._extract_date("2026-03-16T01:00:00+08:00"))

print("\n=== discover_session_jsonl_files：實檔樹、排序、subagents 與 cutoff ===")
with tempfile.TemporaryDirectory() as td:
    root = Path(td) / "projects"
    (root / "a").mkdir(parents=True)
    (root / "a-2").mkdir(parents=True)
    (root / "a" / "subagents").mkdir()
    (root / "dir.jsonl").mkdir()
    (root / "real-target").mkdir()

    f_old = root / "a" / "old.jsonl"
    f_exact = root / "a" / "exact.jsonl"
    f_new = root / "a" / "new.jsonl"
    f_dash = root / "a-2" / "dash.jsonl"
    f_sub = root / "a" / "subagents" / "skip.jsonl"
    f_in_jsonl_dir = root / "dir.jsonl" / "inner.jsonl"
    f_txt = root / "a" / "notes.txt"
    f_target = root / "real-target" / "inside.jsonl"

    for fp in (f_old, f_exact, f_new, f_dash, f_sub, f_in_jsonl_dir, f_txt, f_target):
        fp.write_text("{}\n", encoding="utf-8")

    cutoff = 1_750_000_000.0
    os.utime(f_old, (cutoff - 10, cutoff - 10))
    os.utime(f_exact, (cutoff, cutoff))
    os.utime(f_new, (cutoff + 10, cutoff + 10))
    os.utime(f_dash, (cutoff + 10, cutoff + 10))
    os.utime(f_sub, (cutoff + 10, cutoff + 10))
    os.utime(f_in_jsonl_dir, (cutoff + 10, cutoff + 10))
    os.utime(root / "dir.jsonl", (cutoff + 10, cutoff + 10))
    os.utime(f_target, (cutoff + 10, cutoff + 10))

    # Symlinks: to file, broken .jsonl, to directory (must not descend)
    sym_file = root / "a" / "sym-new.jsonl"
    os.symlink(f_new, sym_file)
    sym_broken = root / "a" / "broken.jsonl"
    os.symlink(root / "does-not-exist.jsonl", sym_broken)
    sym_dir = root / "sym-dir"
    os.symlink(root / "real-target", sym_dir)

    want_cutoff = sorted(
        [
            str(f_exact),
            str(f_new),
            str(sym_file),
            str(f_dash),
            str(root / "dir.jsonl"),
            str(f_in_jsonl_dir),
            str(f_target),
        ],
        key=lambda p: p.split(os.sep),
    )
    check(
        "cutoff 數值下 _discover_jsonl_files 結果與排序正確",
        want_cutoff,
        rec._discover_jsonl_files(root, cutoff),
    )
    check(
        "cutoff 數值下 _collect_jsonl_files 與 _discover_jsonl_files 一致",
        want_cutoff,
        trx._collect_jsonl_files(root, cutoff),
    )
    check(
        "路徑段排序：a/exact.jsonl 排在 a-2/dash.jsonl 之前",
        True,
        want_cutoff.index(str(f_exact)) < want_cutoff.index(str(f_dash)),
    )

    # cutoff=None: recurring-errors skips stat() so broken symlink is included;
    # tool-reliability still calls stat() and raises TypeError on >= None.
    got_none = rec._discover_jsonl_files(root, None)
    check("cutoff=None 時 _discover_jsonl_files 含舊檔與斷裂 symlink", True, str(f_old) in got_none and str(sym_broken) in got_none)
    check("cutoff=None 時 _discover_jsonl_files 仍略過 subagents", False, str(f_sub) in got_none)
    raised_type_error = False
    try:
        trx._collect_jsonl_files(root, None)
    except TypeError:
        raised_type_error = True
    check("cutoff=None 時 _collect_jsonl_files 維持 TypeError 契約", True, raised_type_error)

    # Root under subagents returns []
    check("root 結尾為 /subagents 回傳空串列", [], rec._discover_jsonl_files(root / "a" / "subagents", None))
    check("root 結尾為 /subagents（collect）回傳空串列", [], trx._collect_jsonl_files(root / "a" / "subagents", cutoff))
    check("不存在的 root 回傳空串列", [], rec._discover_jsonl_files(root / "missing", None))

    # bytes root parity
    check("_collect_jsonl_files 傳入 bytes root 回傳空串列", [], trx._collect_jsonl_files(os.fsencode(root), cutoff))
    bytes_type_error = False
    try:
        rec._discover_jsonl_files(os.fsencode(root), cutoff)
    except TypeError:
        bytes_type_error = True
    check("_discover_jsonl_files 傳入 bytes root 維持 TypeError 契約", True, bytes_type_error)

print("\n=== 合成 DirEntry：呼叫順序與 OSError 隔離契約 ===")


class FakeStat:
    def __init__(self, mtime):
        self.st_mtime = mtime


class FakeEntry:
    def __init__(self, name, path, *, is_dir_val=False, is_dir_err=False, mtime=100.0, stat_err=False, log=None):
        self.name = name
        self.path = path
        self._is_dir_val = is_dir_val
        self._is_dir_err = is_dir_err
        self._mtime = mtime
        self._stat_err = stat_err
        self._log = log

    def is_dir(self, follow_symlinks=True):
        if self._log is not None:
            self._log.append((self.name, "is_dir", follow_symlinks))
        if self._is_dir_err:
            raise OSError("is_dir failed")
        return self._is_dir_val

    def stat(self, follow_symlinks=True):
        if self._log is not None:
            self._log.append((self.name, "stat", follow_symlinks))
        if self._stat_err:
            raise OSError("stat failed")
        return FakeStat(self._mtime)


class FakeScandir:
    def __init__(self, entries):
        self._entries = entries

    def __enter__(self):
        return iter(self._entries)

    def __exit__(self, exc_type, exc, tb):
        return False


log_collect = []
entries_collect = {
    "/r": [
        FakeEntry("subagents", "/r/subagents", is_dir_val=True, log=log_collect),
        FakeEntry("race.jsonl", "/r/race.jsonl", is_dir_err=True, mtime=200.0, log=log_collect),
        FakeEntry("dir-stat-err.jsonl", "/r/dir-stat-err.jsonl", is_dir_val=True, stat_err=True, log=log_collect),
        FakeEntry("ok.jsonl", "/r/ok.jsonl", is_dir_val=False, mtime=200.0, log=log_collect),
    ],
    "/r/dir-stat-err.jsonl": [
        FakeEntry("child.jsonl", "/r/dir-stat-err.jsonl/child.jsonl", is_dir_val=False, mtime=200.0, log=log_collect),
    ],
}
with mock.patch("os.scandir", side_effect=lambda p: FakeScandir(entries_collect.get(p, []))):
    got_c = trx._collect_jsonl_files("/r", 100.0)
check("_collect_jsonl_files 在 is_dir 失敗時略過 race.jsonl，stat 失敗的 .jsonl 目錄仍下鑽", ["/r/dir-stat-err.jsonl/child.jsonl", "/r/ok.jsonl"], got_c)
check(
    "_collect_jsonl_files 先 is_dir 後 stat 且對 subagents 呼叫 is_dir",
    [
        ("subagents", "is_dir", False),
        ("race.jsonl", "is_dir", False),
        ("dir-stat-err.jsonl", "is_dir", False),
        ("dir-stat-err.jsonl", "stat", True),
        ("ok.jsonl", "is_dir", False),
        ("ok.jsonl", "stat", True),
        ("child.jsonl", "is_dir", False),
        ("child.jsonl", "stat", True),
    ],
    log_collect,
)

log_discover = []
entries_discover = {
    "/r": [
        FakeEntry("subagents", "/r/subagents", is_dir_val=True, log=log_discover),
        FakeEntry("race.jsonl", "/r/race.jsonl", is_dir_err=True, mtime=200.0, log=log_discover),
        FakeEntry("dir-stat-err.jsonl", "/r/dir-stat-err.jsonl", is_dir_val=True, stat_err=True, log=log_discover),
        FakeEntry("ok.jsonl", "/r/ok.jsonl", is_dir_val=False, mtime=200.0, log=log_discover),
    ],
    "/r/dir-stat-err.jsonl": [
        FakeEntry("child.jsonl", "/r/dir-stat-err.jsonl/child.jsonl", is_dir_val=False, mtime=200.0, log=log_discover),
    ],
}
with mock.patch("os.scandir", side_effect=lambda p: FakeScandir(entries_discover.get(p, []))):
    got_d = rec._discover_jsonl_files("/r", 100.0)
check("_discover_jsonl_files 在 is_dir 失敗時保留 race.jsonl，stat 失敗的 .jsonl 目錄仍下鑽", ["/r/dir-stat-err.jsonl/child.jsonl", "/r/ok.jsonl", "/r/race.jsonl"], got_d)
check(
    "_discover_jsonl_files 略過 subagents 的 is_dir 且對 .jsonl 先 stat 後 is_dir",
    [
        ("race.jsonl", "stat", True),
        ("race.jsonl", "is_dir", False),
        ("dir-stat-err.jsonl", "stat", True),
        ("dir-stat-err.jsonl", "is_dir", False),
        ("ok.jsonl", "stat", True),
        ("ok.jsonl", "is_dir", False),
        ("child.jsonl", "stat", True),
        ("child.jsonl", "is_dir", False),
    ],
    log_discover,
)

print("\n=== python3 -I 隔離模式啟動驗證 ===")
for script_name in ("tool-reliability-extract.py", "recurring-errors-extract.py"):
    res = subprocess.run(
        [sys.executable, "-I", "-B", str(SCRIPT_DIR / script_name), "--help"],
        capture_output=True,
        text=True,
    )
    check(f"python3 -I -B {script_name} --help 成功", 0, res.returncode)

print(f"\n---- {PASS} passed, {FAIL} failed ----")
sys.exit(1 if FAIL else 0)
