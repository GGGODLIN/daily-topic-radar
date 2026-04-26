"""Hacker News fetcher via Algolia API."""
from datetime import datetime, timedelta

import httpx

from social_info._time import utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

API_URL = "https://hn.algolia.com/api/v1/search_by_date"


def _matches_any_keyword(title: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    lower = title.lower()
    return any(k.lower() in lower for k in keywords)


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    keywords = source.params.get("keywords", [])
    limit = source.params.get("limit", 30)
    since_ts = int((utcnow() - timedelta(hours=24)).timestamp())

    resp = await http.get(
        API_URL,
        params={
            "tags": "story",
            "numericFilters": f"created_at_i>{since_ts}",
            "hitsPerPage": limit,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()

    items: list[Item] = []
    now = utcnow()
    for hit in data.get("hits", []):
        title = hit.get("title") or ""
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        if not title or not url:
            continue
        if not _matches_any_keyword(title, keywords):
            continue
        try:
            posted_at = datetime.fromisoformat(
                hit["created_at"].replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except (KeyError, ValueError):
            posted_at = now
        items.append(Item(
            title=title,
            url=url,
            canonical_url=canonical_url(url),
            source="hn",
            source_handle="front_page",
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=hit.get("author") or "",
            excerpt="",
            language="en",
            engagement={
                "score": int(hit.get("points") or 0),
                "comments": int(hit.get("num_comments") or 0),
            },
        ))
    return items
