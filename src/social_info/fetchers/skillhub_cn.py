"""Tencent SkillHub (skillhub.cn) public API fetcher — China-region skill activity."""
from datetime import datetime

import httpx

from social_info._time import utcfromtimestamp, utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

BASE_URL = "https://api.skillhub.cn"
SITE_URL = "https://skillhub.cn"
PAGE_SIZE = 100
USER_AGENT = "social-info/0.1 (daily AI raw aggregator; personal use)"


def _epoch_ms_to_dt(value: object, fallback: datetime) -> datetime:
    if not isinstance(value, (int, float)) or value <= 0:
        return fallback
    try:
        return utcfromtimestamp(value / 1000)
    except (OverflowError, OSError, ValueError):
        return fallback


def _skill_url(entry: dict, slug: str) -> str:
    namespace = entry.get("namespace") or {}
    handle = namespace.get("handle")
    if handle:
        return f"{SITE_URL}/{handle}/{slug}"
    return f"{SITE_URL}/skills/{slug}"


def _to_item(entry: dict, rank: int, tier: int, language: str, now: datetime) -> Item | None:
    slug = entry.get("slug")
    if not slug:
        return None
    title = entry.get("name") or slug
    url = _skill_url(entry, slug)
    engagement = {
        "rank": rank,
        "downloads": int(entry.get("downloads") or 0),
        "installs": int(entry.get("installs") or 0),
        "stars": int(entry.get("stars") or 0),
    }
    excerpt = entry.get("description_zh") or entry.get("description") or ""
    upstream = entry.get("upstream_url")
    also = (
        [{"source": "upstream", "source_handle": str(entry.get("source") or ""), "url": upstream}]
        if upstream
        else []
    )
    return Item(
        title=title,
        url=url,
        canonical_url=canonical_url(url),
        source="skillhub_cn",
        source_handle=f"rank-{rank}",
        source_tier=tier,
        posted_at=_epoch_ms_to_dt(entry.get("updated_at"), now),
        fetched_at=now,
        author=str(entry.get("ownerName") or ""),
        excerpt=" ".join(str(excerpt).split())[:200],
        language=language,
        engagement=engagement,
        also_appeared_in=also,
    )


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    limit = source.params.get("limit", 100)
    source_filter = source.params.get("source_filter")
    language = source.language or "zh-CN"
    items: list[Item] = []
    now = utcnow()
    page = 1

    while len(items) < limit:
        params: dict[str, object] = {
            "page": page,
            "pageSize": PAGE_SIZE,
            "sortBy": "downloads",
        }
        if source_filter:
            params["source"] = source_filter
        resp = await http.get(
            f"{BASE_URL}/api/skills",
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=30.0,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != 0:
            raise ValueError(f"skillhub_cn: API returned code {payload.get('code')}")
        entries = (payload.get("data") or {}).get("skills") or []
        if not entries:
            break
        for entry in entries:
            item = _to_item(entry, len(items) + 1, source.tier, language, now)
            if item is not None:
                items.append(item)
            if len(items) >= limit:
                break
        page += 1

    return items
