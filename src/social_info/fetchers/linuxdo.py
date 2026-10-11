"""LINUX DO latest topics via the public Discourse RSS feed.

httpx is rejected by the site's edge challenge while the system curl binary is
served the feed. curl is the transport only; a challenge or a 429 stops the
source and is not retried. /latest.rss already spans public categories.
"""
import asyncio
import re
import subprocess
from datetime import datetime, timedelta
from urllib.parse import urlencode

import feedparser

from social_info._time import utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

FEED_URL = "https://linux.do/latest.rss"
USER_AGENT = "social-info/0.1 (daily AI raw aggregator; personal use)"
# A short page inside the window is not proof that collection is complete.
PAGE_SIZE = 100
DEFAULT_WINDOW_HOURS = 24
DEFAULT_MAX_PAGES = 50
REQUEST_GAP_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 25
# per_page=100 lowers the request count. The cap remains 50 pages:
# 50 * 25s timeout + 49 * 30s gap = 2720s. 3000s covers that; the runner
# hard limit is 3300s. Hitting this bound fails the source instead of
# paging until the day is covered.
FETCH_BUDGET_SECONDS = 3000

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_FOOTER_RE = re.compile(
    r"\d+\s*个帖子\s*-\s*\d+\s*位参与者|阅读完整话题|Read\s+the\s+full\s+topic",
    re.IGNORECASE,
)


class LinuxDoFetchError(RuntimeError):
    """Raised when the feed cannot be read completely inside the window."""


def _strip_html(markup: str) -> str:
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", markup or "")).strip()
    kept = []
    for part in re.split(r"(?<=[。！？.!?])\s+|\n+", text):
        if _FOOTER_RE.search(part):
            break
        kept.append(part)
    return _WS_RE.sub(" ", " ".join(kept)).strip()


def _fetch_page_text(page: int) -> str:
    """Sync curl. Kept off the event loop via asyncio.to_thread."""
    url = f"{FEED_URL}?{urlencode({'page': page, 'per_page': PAGE_SIZE})}"
    try:
        result = subprocess.run(
            [
                "curl",
                "-sS",
                "-A",
                USER_AGENT,
                "--max-time",
                str(REQUEST_TIMEOUT_SECONDS),
                "-w",
                "\n__SOCIAL_INFO_HTTP__%{http_code}",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=REQUEST_TIMEOUT_SECONDS + 5,
        )
    except subprocess.TimeoutExpired as exc:
        raise LinuxDoFetchError(f"curl timed out on page {page}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise LinuxDoFetchError(f"curl failed on page {page}: {detail[:300]}")
    # -w appends the code with no newline. A marker keeps a body that itself
    # ends in a status-shaped line from being read as the HTTP code.
    marker = "\n__SOCIAL_INFO_HTTP__"
    if marker not in result.stdout:
        raise LinuxDoFetchError(f"linux.do page {page} had no HTTP status")
    body, _, status_text = result.stdout.rpartition(marker)
    try:
        status = int(status_text.strip())
    except ValueError:
        status = 0
    if status == 429:
        raise LinuxDoFetchError(f"linux.do rate limited (429) on page {page}; stopped")
    if status != 200:
        raise LinuxDoFetchError(f"linux.do HTTP {status} on page {page}")
    return body


def _entry_posted_at(entry, page: int, index: int) -> datetime:
    # feedparser's *_parsed is UTC struct_time. mktime would read it as local.
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        raise LinuxDoFetchError(
            f"linux.do page {page} entry {index} has no pubDate"
        )
    return datetime(*parsed[:6])


def _parse_page(body: str, source: SourceConfig, now: datetime, page: int) -> tuple[list[Item], int]:
    """Return items and the raw entry count. Count is not the kept-item count."""
    if "<rss" not in body or "<channel" not in body:
        raise LinuxDoFetchError(f"linux.do page {page} was not an RSS feed")
    parsed = feedparser.parse(body)
    if getattr(parsed, "bozo", False):
        raise LinuxDoFetchError(f"linux.do page {page} was not valid RSS")
    entries = list(parsed.entries)
    items: list[Item] = []
    for index, entry in enumerate(entries):
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            raise LinuxDoFetchError(
                f"linux.do page {page} entry {index} has no title or link"
            )
        posted_at = _entry_posted_at(entry, page, index)
        category = (entry.get("tags") or [{}])[0].get("term", "") if entry.get("tags") else ""
        items.append(Item(
            title=title,
            url=link,
            canonical_url=canonical_url(link),
            source="linuxdo",
            source_handle=category or "latest",
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=entry.get("author") or "",
            excerpt=_strip_html(entry.get("summary") or entry.get("description") or "")[:200],
            language=source.language,
            engagement={},
        ))
    return items, len(entries)


def _assert_page_moves_backward(page: int, page_items: list[Item], previous_oldest: datetime | None) -> None:
    dates = [item.posted_at for item in page_items]
    if dates != sorted(dates, reverse=True):
        raise LinuxDoFetchError(f"linux.do page {page} is not newest-first")
    if previous_oldest is not None and dates[-1] >= previous_oldest:
        raise LinuxDoFetchError(f"linux.do page {page} did not advance past the previous page")


async def fetch(source: SourceConfig, http=None) -> list[Item]:
    window_hours = source.params.get("window_hours", DEFAULT_WINDOW_HOURS)
    max_pages = source.params.get("max_pages", DEFAULT_MAX_PAGES)
    cutoff = utcnow() - timedelta(hours=window_hours)
    now = utcnow()
    started = utcnow()

    collected: list[Item] = []
    seen_urls: set[str] = set()
    previous_oldest: datetime | None = None
    for page in range(max_pages):
        # Gap and the request timeout both count. A check only before the
        # gap lets the last page run past the budget by a full timeout.
        needed = REQUEST_TIMEOUT_SECONDS + (REQUEST_GAP_SECONDS if page else 0)
        if (utcnow() - started).total_seconds() + needed > FETCH_BUDGET_SECONDS:
            raise LinuxDoFetchError(
                f"linux.do fetch budget of {FETCH_BUDGET_SECONDS}s exhausted before the "
                f"{window_hours}h window ended; not a complete day"
            )
        if page:
            await asyncio.sleep(REQUEST_GAP_SECONDS)
        body = await asyncio.to_thread(_fetch_page_text, page)
        page_items, raw_count = _parse_page(body, source, now, page)
        if raw_count == 0:
            raise LinuxDoFetchError(f"linux.do page {page} had no entries")
        page_urls = [item.canonical_url for item in page_items]
        if len(page_urls) != len(set(page_urls)):
            raise LinuxDoFetchError(f"linux.do page {page} repeats an entry")
        if seen_urls and set(page_urls) <= seen_urls:
            raise LinuxDoFetchError(f"linux.do page {page} repeated the previous page")
        _assert_page_moves_backward(page, page_items, previous_oldest)
        fresh = [item for item in page_items if item.canonical_url not in seen_urls]
        seen_urls.update(item.canonical_url for item in fresh)
        collected.extend(item for item in fresh if item.posted_at >= cutoff)
        previous_oldest = min(item.posted_at for item in page_items)
        if previous_oldest < cutoff:
            return collected
        # A short raw page can be a truncated day, so it does not end the feed.
        if raw_count < PAGE_SIZE:
            raise LinuxDoFetchError(
                f"linux.do page {page} is short and still inside the "
                f"{window_hours}h window; not treating it as the end of the feed"
            )
    raise LinuxDoFetchError(
        f"linux.do still inside the {window_hours}h window after {max_pages} pages; "
        "refusing to truncate silently"
    )
