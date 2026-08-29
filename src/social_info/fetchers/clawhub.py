"""ClawHub (OpenClaw skill registry) public API fetcher."""
from datetime import datetime

import httpx

from social_info._time import utcfromtimestamp, utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

BASE_URL = "https://clawhub.ai"
PAGE_SIZE = 100
USER_AGENT = "social-info/0.1 (daily AI raw aggregator; personal use)"


def _epoch_ms_to_dt(value: object, fallback: datetime) -> datetime:
    if not isinstance(value, (int, float)) or value <= 0:
        return fallback
    try:
        return utcfromtimestamp(value / 1000)
    except (OverflowError, OSError, ValueError):
        return fallback


def _to_item(entry: dict, rank: int, tier: int, now: datetime) -> Item | None:
    slug = entry.get("slug")
    if not slug:
        return None
    title = entry.get("displayName") or slug
    url = f"{BASE_URL}/skills/{slug}"
    stats = entry.get("stats") or {}
    engagement = {
        "rank": rank,
        "downloads": int(stats.get("downloads") or 0),
        "installs": int(stats.get("installs") or 0),
        "stars": int(stats.get("stars") or 0),
        "comments": int(stats.get("comments") or 0),
        "versions": int(stats.get("versions") or 0),
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
    )


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    limit = source.params.get("limit", 100)
    items: list[Item] = []
    cursor: str | None = None
    now = utcnow()

    while len(items) < limit:
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
        entries = payload.get("items") or payload.get("skills") or []
        if not entries:
            break
        for entry in entries:
            item = _to_item(entry, len(items) + 1, source.tier, now)
            if item is not None:
                items.append(item)
            if len(items) >= limit:
                break
        cursor = payload.get("nextCursor")
        if not cursor:
            break

    return items
