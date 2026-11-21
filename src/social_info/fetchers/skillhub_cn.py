"""Tencent SkillHub (skillhub.cn) public API fetcher — China-region skill activity."""
import math
from datetime import datetime

import httpx

from social_info._time import safe_utcfromtimestamp, utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

BASE_URL = "https://api.skillhub.cn"
SITE_URL = "https://skillhub.cn"
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


def _skill_url(entry: dict, slug: str) -> str:
    namespace = entry.get("namespace")
    handle = namespace.get("handle") if isinstance(namespace, dict) else None
    if handle:
        return f"{SITE_URL}/{handle}/{slug}"
    return f"{SITE_URL}/skills/{slug}"


def _to_item(entry: dict, rank: int, tier: int, language: str, now: datetime) -> Item | None:
    slug = entry.get("slug")
    if not isinstance(slug, str) or not slug:
        return None
    title = str(entry.get("name") or slug)
    url = _skill_url(entry, slug)
    engagement = {
        "rank": rank,
        "downloads": _safe_int(entry.get("downloads")),
        "installs": _safe_int(entry.get("installs")),
        "stars": _safe_int(entry.get("stars")),
    }
    excerpt = entry.get("description_zh") or entry.get("description") or ""
    upstream = entry.get("upstream_url")
    also = (
        [{"source": "upstream", "source_handle": str(entry.get("source") or ""), "url": upstream}]
        if isinstance(upstream, str) and upstream
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
        metadata={"content_kind": "description"},
    )


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    limit = source.params.get("limit", 100)
    source_filter = source.params.get("source_filter")
    language = source.language or "zh-CN"
    items: list[Item] = []
    now = utcnow()
    page = 1

    while len(items) < limit and page <= MAX_PAGES:
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
        if not isinstance(payload, dict):
            break
        if payload.get("code") != 0:
            raise ValueError(f"skillhub_cn: API returned code {payload.get('code')}")
        data = payload.get("data")
        if not isinstance(data, dict):
            break
        entries = data.get("skills")
        if not isinstance(entries, list) or not entries:
            break
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            item = _to_item(entry, len(items) + 1, source.tier, language, now)
            if item is not None:
                items.append(item)
            if len(items) >= limit:
                break
        if len(entries) < PAGE_SIZE:
            break
        page += 1

    return items
