"""Threads posts collected by the muse.ai agent, read from a local spool file.

The Apify Threads source is disabled for cost, so a Muse job in the watchdogs
repo (watchers/llm-free-token-watch/muse_threads.py with --prompt-file
muse/threads-radar-prompt.txt) searches Threads a few times a week and leaves
the spool; this fetcher only reads it. Re-reading the same spool on the daily
runs in between is harmless: dedup skips rows it has already seen.
"""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from social_info._time import parse_iso_utc, utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

_HANDLE_RE = re.compile(r"threads\.(?:com|net)/(@[A-Za-z0-9._]+)/")
_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿]")


class MuseSpoolStale(RuntimeError):
    pass


def _parse_time(value: object, fallback: datetime | None = None) -> datetime:
    # social_info stores naive UTC everywhere (see _time.utcnow); an aware value
    # here would break the age arithmetic against utcnow().
    return parse_iso_utc(value, fallback if fallback is not None else utcnow())


def items_from_spool(spool: dict, source: SourceConfig, now: datetime) -> list[Item]:
    if not isinstance(spool, dict):
        return []
    items = []
    raw_items = spool.get("items")
    if not isinstance(raw_items, list):
        return []
    for record in raw_items:
        if not isinstance(record, dict):
            continue
        url = record.get("url") or ""
        if not isinstance(url, str) or not url:
            continue
        raw_text = record.get("text")
        text = raw_text.strip() if isinstance(raw_text, str) else ""
        if not text:
            continue
        handle = _HANDLE_RE.search(url)
        items.append(Item(
            title=text[:120] + ("…" if len(text) > 120 else ""),
            url=url,
            canonical_url=canonical_url(url),
            source="threads",
            source_handle=handle.group(1) if handle else "",
            source_tier=source.tier,
            posted_at=_parse_time(record.get("ts"), now),
            fetched_at=now,
            excerpt=text[:200],
            language="zh" if _CJK_RE.search(text) else (source.language or "en"),
            content=text,
            metadata={"content_kind": "post_text"},
        ))
    return items


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    path = Path(source.params["path"]).expanduser()
    # Before the first Muse run there is simply nothing to read yet.
    if not path.exists():
        return []
    spool = json.loads(path.read_text())
    if not isinstance(spool, dict):
        return []
    now = utcnow()
    age = now - _parse_time(spool.get("collected_at"), now)
    # Raising (instead of returning []) sends a Muse job that quietly stopped
    # producing into KNOWN_ISSUES like any other failing source.
    if age > timedelta(hours=float(source.params.get("max_age_hours", 110))):
        raise MuseSpoolStale(f"muse spool is {age.total_seconds() / 3600:.0f}h old: {path}")
    return items_from_spool(spool, source, now)
