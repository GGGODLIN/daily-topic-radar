"""ClawHub (OpenClaw skill registry) public API fetcher."""
import math
from datetime import datetime

import httpx

from social_info._time import safe_utcfromtimestamp, utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

BASE_URL = "https://clawhub.ai"
PAGE_SIZE = 100
MAX_PAGES = 20
USER_AGENT = "social-info/0.1 (daily AI raw aggregator; personal use)"


def _epoch_ms_to_dt(value: object, fallback: datetime) -> datetime:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        return fallback
    return safe_utcfromtimestamp(value / 1000, fallback)


def _safe_int(value: object) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return 0


def _to_item(entry: dict, rank: int, tier: int, now: datetime) -> Item | None:
    slug = entry.get("slug")
    if not isinstance(slug, str) or not slug:
        return None
    title = str(entry.get("displayName") or slug)
    url = f"{BASE_URL}/skills/{slug}"
    raw_stats = entry.get("stats")
    stats = raw_stats if isinstance(raw_stats, dict) else {}
    engagement = {
        "rank": rank,
        "downloads": _safe_int(stats.get("downloads")),
        "installs": _safe_int(stats.get("installs")),
        "stars": _safe_int(stats.get("stars")),
        "comments": _safe_int(stats.get("comments")),
        "versions": _safe_int(stats.get("versions")),
    }
    return Item(
        title=title,
        url=url,
        canonical_url=canonical_url(url),
        source="clawhub",
        source_handle=f"rank-{rank}",
        source_tier=tier,
        posted_at=_epoch_ms_to_dt(entry.get("updatedAt"), now),
        fetched_at=now,
        author="",
        excerpt=" ".join(str(entry.get("summary") or "").split())[:200],
        language="en",
        engagement=engagement,
        metadata={"content_kind": "summary"},
    )


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    limit = source.params.get("limit", 100)
    items: list[Item] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    now = utcnow()
    pages = 0

    while len(items) < limit and pages < MAX_PAGES:
        pages += 1
        params: dict[str, object] = {
            "sort": "downloads",
            "limit": min(PAGE_SIZE, limit - len(items)),
        }
        if cursor:
            params["cursor"] = cursor
        resp = await http.get(
            f"{BASE_URL}/api/v1/skills",
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=30.0,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            break
        entries = payload.get("items") or payload.get("skills")
        if not isinstance(entries, list) or not entries:
            break
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            item = _to_item(entry, len(items) + 1, source.tier, now)
            if item is not None:
                items.append(item)
            if len(items) >= limit:
                break
        next_cursor = payload.get("nextCursor")
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    return items
