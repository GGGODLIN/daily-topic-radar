"""Reddit fetcher via the public `.rss` listing endpoint.

Path history: unauthenticated `.json` endpoints have returned 403 for a long
time; on 2026-08-11 `old.reddit.com/r/{sub}/top/` also started answering
unauthenticated requests with a 302 to `/login?reason=lor2`. Because httpx
follows redirects, that returned a 200 login page which parsed to zero posts —
the source went silent without ever landing in failures or stale. The official
Data API is not a fallback: Reddit closed self-service API access on 2025-11-11
and new grants now require manual approval.

`top.rss` still serves publicly, honours `t` and `limit`, and carries title /
permalink / author / timestamp / selftext. It does NOT carry score or comment
count — the ordering of the feed is the only remaining popularity signal, so
items keep feed order and expose an empty engagement map.

Reddit throttles this endpoint hard (429 after a couple of rapid requests) and
the pipeline runs every source concurrently, so requests are serialised through
a module-level lock with a minimum gap, plus one long backoff retry on 429.
"""
import asyncio
import calendar
import html
import re
import time

import feedparser
import httpx

from social_info._time import utcfromtimestamp, utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

USER_AGENT = "social-info/0.1 (daily AI raw aggregator; personal use)"

MIN_REQUEST_GAP_SECONDS = 45.0
THROTTLED_RETRY_DELAY_SECONDS = 90.0
MAX_THROTTLED_RETRIES = 1
TOTAL_BUDGET_SECONDS = 300.0
EXCERPT_MAX_CHARS = 200

_REDDIT_INTERNAL_PREFIXES = (
    "https://www.reddit.com",
    "https://reddit.com",
    "https://old.reddit.com",
    "https://i.redd.it",
    "https://v.redd.it",
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_OUTBOUND_LINK_RE = re.compile(r'<a href="([^"]+)">\[link\]</a>')
_SUBMITTED_BY_RE = re.compile(r"<!--\s*SC_ON\s*-->|submitted by")
_USER_PREFIX = "/u/"

_request_lock = asyncio.Lock()
_last_request_finished_at: float | None = None
_first_request_started_at: float | None = None


def _strip_html(markup: str) -> str:
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub("", markup))).strip()


def _outbound_url(markup: str) -> str:
    match = _OUTBOUND_LINK_RE.search(markup)
    if not match:
        return ""
    url = html.unescape(match.group(1))
    return "" if url.startswith(_REDDIT_INTERNAL_PREFIXES) else url


def _strip_user_prefix(author: str) -> str:
    return author[len(_USER_PREFIX):] if author.startswith(_USER_PREFIX) else author


def _entry_excerpt(entry) -> str:
    markup = ""
    entry_content = entry.get("content")
    if entry_content:
        markup = entry_content[0].get("value", "") or ""
    markup = markup or entry.get("summary") or ""

    outbound = _outbound_url(markup)
    if outbound:
        return f"src [{outbound}]({outbound})"

    body = _strip_html(_SUBMITTED_BY_RE.split(markup)[0])
    return body[:EXCERPT_MAX_CHARS]


def _entry_posted_at(entry, fallback):
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return utcfromtimestamp(calendar.timegm(parsed))
    return fallback


def _budget_left() -> float:
    if _first_request_started_at is None:
        return TOTAL_BUDGET_SECONDS
    return TOTAL_BUDGET_SECONDS - (time.monotonic() - _first_request_started_at)


async def _get_serialised(http: httpx.AsyncClient, url: str, params: dict) -> httpx.Response:
    global _last_request_finished_at, _first_request_started_at
    async with _request_lock:
        if _first_request_started_at is None:
            _first_request_started_at = time.monotonic()
        elif _last_request_finished_at is not None:
            wait = MIN_REQUEST_GAP_SECONDS - (time.monotonic() - _last_request_finished_at)
            if wait > 0:
                await asyncio.sleep(min(wait, max(_budget_left(), 0.0)))
        try:
            return await http.get(
                url,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=30.0,
            )
        finally:
            _last_request_finished_at = time.monotonic()


async def _fetch_feed_text(http: httpx.AsyncClient, url: str, params: dict) -> str:
    resp = await _get_serialised(http, url, params)
    for _ in range(MAX_THROTTLED_RETRIES):
        if resp.status_code != httpx.codes.TOO_MANY_REQUESTS:
            break
        if _budget_left() < THROTTLED_RETRY_DELAY_SECONDS:
            break
        await asyncio.sleep(THROTTLED_RETRY_DELAY_SECONDS)
        resp = await _get_serialised(http, url, params)
    resp.raise_for_status()
    return resp.text


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    subreddit = source.params["subreddit"]
    time_window = source.params.get("time_window", "day")
    limit = source.params.get("limit", 10)

    url = f"https://www.reddit.com/r/{subreddit}/top.rss"
    text = await _fetch_feed_text(http, url, {"t": time_window, "limit": limit})
    parsed = feedparser.parse(text)

    items: list[Item] = []
    now = utcnow()
    for entry in parsed.entries[:limit]:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        items.append(Item(
            title=title,
            url=link,
            canonical_url=canonical_url(link),
            source="reddit",
            source_handle=f"r/{subreddit}",
            source_tier=source.tier,
            posted_at=_entry_posted_at(entry, now),
            fetched_at=now,
            author=_strip_user_prefix((entry.get("author") or "").strip()),
            excerpt=_entry_excerpt(entry),
            language="en",
            engagement={},
        ))
    return items
