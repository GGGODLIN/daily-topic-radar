"""Threads (Meta) fetcher.

Three modes:
- keyword: /keyword_search with q=<term>, search_type=TOP|RECENT
- tag:     /keyword_search with q=<tag>, search_mode=TAG
- user:    skeleton only, disabled until handles provided
"""
import os
from datetime import datetime, timedelta

import httpx

from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

API_BASE = "https://graph.threads.net/v1.0"

_FIELDS = "id,text,permalink,username,timestamp,like_count,replies_count"


async def _search_one(
    http: httpx.AsyncClient,
    query: str,
    search_type: str,
    search_mode: str | None,
    since_iso: str,
    limit: int,
    token: str,
) -> list[dict]:
    params = {
        "q": query,
        "search_type": search_type,
        "fields": _FIELDS,
        "limit": limit,
        "since": since_iso,
        "access_token": token,
    }
    if search_mode:
        params["search_mode"] = search_mode
    resp = await http.get(f"{API_BASE}/keyword_search", params=params, timeout=30.0)
    resp.raise_for_status()
    return resp.json().get("data", [])


def _post_to_item(
    post: dict, source_tier: int, language: str, source_handle: str
) -> Item:
    text = (post.get("text") or "").strip()
    url = post.get("permalink") or f"https://www.threads.net/post/{post.get('id')}"
    try:
        posted_at = datetime.strptime(
            post["timestamp"], "%Y-%m-%dT%H:%M:%S%z"
        ).replace(tzinfo=None)
    except (KeyError, ValueError):
        posted_at = datetime.utcnow()
    username = post.get("username") or ""
    return Item(
        title=text[:120] + ("…" if len(text) > 120 else ""),
        url=url,
        canonical_url=canonical_url(url),
        source="threads",
        source_handle=source_handle or (f"@{username}" if username else ""),
        source_tier=source_tier,
        posted_at=posted_at,
        fetched_at=datetime.utcnow(),
        author=username,
        excerpt=text[:200],
        language=language,
        engagement={
            "likes": int(post.get("like_count") or 0),
            "comments": int(post.get("replies_count") or 0),
        },
    )


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("THREADS_ACCESS_TOKEN env var not set")

    mode = source.params.get("mode", "keyword")
    search_type = source.params.get("search_type", "TOP")
    queries = source.params.get("queries", [])
    per_query_limit = source.params.get("per_query_limit", 5)
    window_hours = source.params.get("time_window_hours", 24)
    language = source.language or "en"
    since_iso = (datetime.utcnow() - timedelta(hours=window_hours)).strftime(
        "%Y-%m-%dT%H:%M:%S+0000"
    )

    if mode == "user":
        return []

    search_mode = "TAG" if mode == "tag" else None

    items: list[Item] = []
    for q in queries:
        posts = await _search_one(
            http, q, search_type, search_mode, since_iso, per_query_limit, token
        )
        source_handle = f"{mode}:{q}"
        items.extend(_post_to_item(p, source.tier, language, source_handle) for p in posts)
    return items
