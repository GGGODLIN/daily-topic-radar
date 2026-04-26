"""Threads via Apify watcher.data/search-threads-by-keywords actor.

Multi-keyword single invocation, $8/1000 results pay-per-result, built-in dedup.
Replaces the Meta Threads API path (Dev-mode keyword_search dead-end).
"""
import os

import httpx

from social_info._time import utcfromtimestamp, utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

ACTOR_ID = "D15iJFBNZ9wgeWAhw"
API_URL = f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items"


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    token = os.environ.get("APIFY_TOKEN_THREADS")
    if not token:
        raise RuntimeError("APIFY_TOKEN_THREADS env var not set")

    keywords = source.params.get("queries", [])
    per_query_limit = source.params.get("per_query_limit", 3)
    sort_by_recent = source.params.get("sort_by_recent", True)
    language = source.language or "en"

    if not keywords:
        return []

    payload = {
        "keywords": keywords,
        "maxItemsPerKeyword": per_query_limit,
        "sortByRecent": sort_by_recent,
    }

    resp = await http.post(
        API_URL,
        params={"token": token},
        json=payload,
        timeout=180.0,
    )
    resp.raise_for_status()
    data = resp.json()

    items: list[Item] = []
    now = utcnow()
    for post in data:
        text = (post.get("text") or "").strip()
        url = post.get("url") or ""
        if not text or not url:
            continue
        author = post.get("author") or ""
        ts = post.get("created_at")
        try:
            posted_at = utcfromtimestamp(int(ts)) if ts else now
        except (TypeError, ValueError):
            posted_at = now
        items.append(Item(
            title=text[:120] + ("…" if len(text) > 120 else ""),
            url=url,
            canonical_url=canonical_url(url),
            source="threads",
            source_handle=f"@{author}" if author else "",
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=post.get("author_name") or author,
            excerpt=text[:200],
            language=post.get("lang") or language,
            engagement={
                "likes": int(post.get("like_count") or 0),
                "comments": int(post.get("reply_count") or 0),
                "reposts": int(post.get("repost_count") or 0),
            },
        ))
    return items
