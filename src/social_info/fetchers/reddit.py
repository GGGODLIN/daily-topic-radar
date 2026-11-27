"""Reddit fetcher via the public Arctic Shift search API.

Path history:
- Unauthenticated `.json` endpoints have returned 403 for a long time.
- 2026-08-11: `old.reddit.com/r/{sub}/top/` started redirecting unauthenticated
  requests (302 -> `/login?reason=lor2`).
- 2026-11-13: Reddit permanently ended native RSS feed support (announced in
  the 2026-10-08 changelog; local feeds began returning 404 on 2026-11-18).
  Public Data API self-service also closed in 2025-11 and new registrations
  closed on 2026-10-31.

Arctic Shift (`arctic-shift.photon-reddit.com/api/posts/search`) serves
subreddit posts without OAuth, including `score`, `num_comments`, `selftext`,
and outbound `url`. Its API caps `limit` at 100 and does not support server-side
score sorting, so we paginate backwards with `before` over a 30-hour window
(absorbing the ~2.5-4h score refresh lag on brand-new posts), filter removed
submissions, and rank client-side by `(score, num_comments, created_utc)`.
"""
import asyncio
import html
import re
import time
from datetime import UTC
from typing import Any

import httpx

from social_info._time import safe_utcfromtimestamp, utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

ARCTIC_SHIFT_SEARCH_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"
USER_AGENT = "social-info/0.1 (daily AI raw aggregator; personal use)"

PAGE_LIMIT = 100
MIN_PAGE_LIMIT = 25
MAX_PAGES_PER_SUBREDDIT = 5
DEFAULT_DAY_LOOKBACK_HOURS = 30
_TIME_WINDOW_HOURS: dict[str, int] = {
    "hour": 4,
    "day": DEFAULT_DAY_LOOKBACK_HOURS,
    "week": 168,
    "month": 720,
}

MIN_REQUEST_GAP_SECONDS = 0.5
THROTTLED_RETRY_DELAY_SECONDS = 5.0
MAX_RETRY_WAIT_SECONDS = 30.0
MAX_THROTTLED_RETRIES = 2
TOTAL_BUDGET_SECONDS = 120.0
EXCERPT_MAX_CHARS = 200
ERROR_BODY_PREVIEW_CHARS = 200


class ArcticShiftOverloadError(TimeoutError):
    """Arctic Shift returns HTTP 422 for query timeouts and server overload
    (ArthurHeitmann/arctic_shift#66). Subclassing TimeoutError lets pipeline
    classify_error treat exhausted 422s as transient without relaxing 422 for
    other fetchers.
    """

_REDDIT_INTERNAL_PREFIXES = (
    "https://www.reddit.com",
    "https://reddit.com",
    "https://old.reddit.com",
    "https://i.redd.it",
    "https://v.redd.it",
)

_WS_RE = re.compile(r"\s+")
_USER_PREFIX = "/u/"
_REMOVED_MARKERS = frozenset({"[removed]", "[deleted]"})

_request_lock = asyncio.Lock()
_last_request_finished_at: float | None = None
_first_request_started_at: float | None = None


def _clean_text(text: str) -> str:
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def _safe_int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _strip_user_prefix(author: str) -> str:
    return author[len(_USER_PREFIX):] if author.startswith(_USER_PREFIX) else author


def _is_removed_post(post: dict[str, Any]) -> bool:
    if post.get("removed_by_category"):
        return True
    selftext = (post.get("selftext") or "").strip().lower()
    if selftext in _REMOVED_MARKERS:
        return True
    if not (post.get("title") or "").strip():
        return True
    return False


def _post_url(post: dict[str, Any], subreddit: str) -> str:
    permalink = (post.get("permalink") or "").strip()
    if permalink.startswith("/"):
        return f"https://www.reddit.com{permalink}"
    if permalink.startswith(("http://", "https://")):
        return permalink
    post_id = (post.get("id") or "").strip()
    if post_id:
        return f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/"
    return ""


def _post_outbound_url(post: dict[str, Any]) -> str:
    if post.get("is_self"):
        return ""
    url = html.unescape((post.get("url") or "").strip())
    if not url.startswith(("http://", "https://")):
        return ""
    return "" if url.startswith(_REDDIT_INTERNAL_PREFIXES) else url


def _post_excerpt(post: dict[str, Any]) -> str:
    outbound = _post_outbound_url(post)
    if outbound:
        return f"src [{outbound}]({outbound})"
    body = _clean_text(post.get("selftext") or "")
    return body[:EXCERPT_MAX_CHARS]


def _post_posted_at(post: dict[str, Any], fallback):
    return safe_utcfromtimestamp(post.get("created_utc"), fallback)


def _budget_left() -> float:
    if _first_request_started_at is None:
        return TOTAL_BUDGET_SECONDS
    return TOTAL_BUDGET_SECONDS - (time.monotonic() - _first_request_started_at)


def _retry_delay(resp: httpx.Response) -> float:
    if THROTTLED_RETRY_DELAY_SECONDS <= 0:
        return 0.0
    for header in ("Retry-After", "x-ratelimit-reset"):
        raw = resp.headers.get(header)
        if not raw:
            continue
        try:
            wait = float(raw)
        except ValueError:
            continue
        if wait > 0:
            return min(max(wait, THROTTLED_RETRY_DELAY_SECONDS), MAX_RETRY_WAIT_SECONDS)
    return THROTTLED_RETRY_DELAY_SECONDS


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


def _error_body_preview(resp: httpx.Response) -> str:
    try:
        raw = resp.text or ""
    except Exception:
        return ""
    return _WS_RE.sub(" ", raw).strip()[:ERROR_BODY_PREVIEW_CHARS]


async def _fetch_search_page(
    http: httpx.AsyncClient,
    params: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    req_params = dict(params)
    current_limit = _safe_int(req_params.get("limit")) or PAGE_LIMIT
    req_params["limit"] = current_limit

    resp = await _get_serialised(http, ARCTIC_SHIFT_SEARCH_URL, req_params)
    for _ in range(MAX_THROTTLED_RETRIES):
        if resp.status_code not in (
            httpx.codes.TOO_MANY_REQUESTS,
            httpx.codes.UNPROCESSABLE_ENTITY,
        ):
            break
        wait = _retry_delay(resp)
        if _budget_left() < wait:
            break
        if resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
            floor = min(current_limit, MIN_PAGE_LIMIT)
            current_limit = max(floor, current_limit // 2)
            req_params["limit"] = current_limit
        if wait > 0:
            await asyncio.sleep(wait)
        resp = await _get_serialised(http, ARCTIC_SHIFT_SEARCH_URL, req_params)

    if resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
        preview = _error_body_preview(resp)
        detail = f" ({preview})" if preview else ""
        raise ArcticShiftOverloadError(
            f"HTTP 422 from Arctic Shift for {resp.url}{detail}"
        )
    resp.raise_for_status()
    try:
        payload = resp.json()
    except ValueError:
        return [], current_limit
    if not isinstance(payload, dict):
        return [], current_limit
    data = payload.get("data")
    if not isinstance(data, list):
        return [], current_limit
    return [row for row in data if isinstance(row, dict)], current_limit


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    subreddit = source.params["subreddit"]
    time_window = str(source.params.get("time_window", "day"))
    limit = int(source.params.get("limit", 10))
    max_pages = int(source.params.get("max_pages", MAX_PAGES_PER_SUBREDDIT))

    now = utcnow()
    now_ts = int(now.replace(tzinfo=UTC).timestamp())
    lookback_hours = _TIME_WINDOW_HOURS.get(time_window, DEFAULT_DAY_LOOKBACK_HOURS)
    after_ts = now_ts - lookback_hours * 3600

    seen_ids: set[str] = set()
    candidates: list[dict[str, Any]] = []
    before_ts: int | None = None
    page_limit = PAGE_LIMIT

    for _ in range(max(1, max_pages)):
        params: dict[str, Any] = {
            "subreddit": subreddit,
            "after": after_ts,
            "limit": page_limit,
            "sort": "desc",
        }
        if before_ts is not None:
            params["before"] = before_ts

        batch, page_limit = await _fetch_search_page(http, params)
        if not batch:
            break

        oldest_ts: int | None = None
        for post in batch:
            created = _safe_int(post.get("created_utc"))
            if created > 0 and (oldest_ts is None or created < oldest_ts):
                oldest_ts = created
            post_id = str(post.get("id") or "")
            if post_id:
                if post_id in seen_ids:
                    continue
                seen_ids.add(post_id)
            candidates.append(post)

        if len(batch) < page_limit or oldest_ts is None:
            break
        next_before = oldest_ts - 1
        if next_before <= after_ts or (before_ts is not None and next_before >= before_ts):
            break
        before_ts = next_before

    usable = [p for p in candidates if not _is_removed_post(p) and _post_url(p, subreddit)]
    usable.sort(
        key=lambda p: (
            _safe_int(p.get("score")),
            _safe_int(p.get("num_comments")),
            _safe_int(p.get("created_utc")),
        ),
        reverse=True,
    )

    items: list[Item] = []
    for post in usable[:limit]:
        title = _clean_text(str(post.get("title") or ""))
        link = _post_url(post, subreddit)
        if not title or not link:
            continue
        post_id = str(post.get("id") or "").strip()
        metadata: dict[str, Any] = {"content_kind": "summary"}
        if post_id:
            metadata["id"] = post_id
        items.append(Item(
            title=title,
            url=link,
            canonical_url=canonical_url(link),
            source="reddit",
            source_handle=f"r/{subreddit}",
            source_tier=source.tier,
            posted_at=_post_posted_at(post, now),
            fetched_at=now,
            author=_strip_user_prefix(str(post.get("author") or "").strip()),
            excerpt=_post_excerpt(post),
            language="en",
            engagement={
                "score": _safe_int(post.get("score")),
                "comments": _safe_int(post.get("num_comments")),
            },
            metadata=metadata,
        ))
    return items
