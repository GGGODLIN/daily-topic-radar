"""Shared helpers for Claude session JSONL discovery and UTC timestamp parsing.

Single source of truth for:
- scripts/local-analysis/tool-reliability-extract.py
- scripts/local-analysis/recurring-errors-extract.py
"""

import datetime as dt
import os


def parse_iso_utc(ts):
    """Parse an ISO-8601 timestamp string and normalize aware values to UTC.

    Preserves naive datetimes as naive; returns None on non-string, blank,
    malformed, or out-of-range inputs.
    """
    if not isinstance(ts, str) or not (s := ts.strip()):
        return None
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        d = dt.datetime.fromisoformat(s)
        if d.tzinfo is not None:
            d = d.astimezone(dt.timezone.utc)
    except (ValueError, OverflowError):
        return None
    return d


def discover_session_jsonl_files(root, cutoff=None, *, check_dir_first=False):
    """Return sorted .jsonl paths under root, skipping subagents directories.

    Matches Path.rglob component-wise path ordering via p.split(os.sep).
    When check_dir_first is False (default), cutoff=None skips stat(), .jsonl
    stat/is_dir errors are isolated, and entries named "subagents" skip is_dir().
    When check_dir_first is True, is_dir(follow_symlinks=False) gates every
    entry before stat(follow_symlinks=True) and st_mtime >= cutoff (passing
    cutoff=None raises TypeError whenever a .jsonl entry's stat() succeeds).
    """
    root_s = os.fspath(root)
    if "/subagents/" in (root_s + "/"):
        return []
    found = []
    stack = [root_s]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    name = entry.name
                    is_jsonl = name.endswith(".jsonl")
                    if check_dir_first:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if name == "subagents":
                                    continue
                                stack.append(entry.path)
                        except OSError:
                            continue
                        if is_jsonl:
                            try:
                                st = entry.stat(follow_symlinks=True)
                            except OSError:
                                continue
                            if st.st_mtime >= cutoff:
                                found.append(entry.path)
                    else:
                        if is_jsonl:
                            try:
                                # 檔案可能在游標所記錄的同一秒內追加資料，等於游標時也要重掃。
                                if cutoff is None or entry.stat().st_mtime >= cutoff:
                                    found.append(entry.path)
                            except OSError:
                                pass
                        elif name == "subagents":
                            continue
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                        except OSError:
                            pass
        except OSError:
            continue
    found.sort(key=lambda p: p.split(os.sep))
    return found
