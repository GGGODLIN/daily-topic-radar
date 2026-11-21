"""Hacker News fetcher via Algolia API, comments enrichment via Firebase API."""
import asyncio
import html
import re
from datetime import timedelta

import httpx

from social_info._time import parse_iso_utc, safe_utcfromtimestamp, utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

API_URL = "https://hn.algolia.com/api/v1/search_by_date"
FIREBASE_URL = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
MAX_COMMENTS = 5
COMMENT_MAX_CHARS = 300

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _matches_any_keyword(title: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    lower = title.lower()
    return any(k.lower() in lower for k in keywords)


def _clean_comment_text(raw: str) -> str:
    stripped = _WS_RE.sub(" ", _TAG_RE.sub("", raw or "")).strip()
    text = html.unescape(stripped)
    if len(text) > COMMENT_MAX_CHARS:
        return text[:COMMENT_MAX_CHARS] + "…"
    return text


async def _fetch_one_comment(http: httpx.AsyncClient, comment_id: int) -> dict | None:
    try:
        resp = await http.get(FIREBASE_URL.format(id=comment_id), timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("deleted") or data.get("dead"):
        return None
    raw_by = data.get("by") or ""
    by = raw_by if isinstance(raw_by, str) else str(raw_by)
    raw_text = data.get("text")
    text = _clean_comment_text(raw_text if isinstance(raw_text, str) else "")
    if not by or not text:
        return None
    ts = data.get("time")
    dt = safe_utcfromtimestamp(ts, None) if ts else None
    posted_at = dt.replace(microsecond=0).isoformat() if dt is not None else ""
    return {"author": by, "text": text, "posted_at": posted_at}


async def _fetch_comments_for_story(
    http: httpx.AsyncClient, story_id: str
) -> list[dict]:
    try:
        resp = await http.get(FIREBASE_URL.format(id=story_id), timeout=15.0)
        resp.raise_for_status()
        story = resp.json()
    except Exception:
        return []
    if not isinstance(story, dict):
        return []
    raw_kids = story.get("kids")
    if not isinstance(raw_kids, list):
        return []
    kids = raw_kids[:MAX_COMMENTS]
    if not kids:
        return []
    fetched = await asyncio.gather(*[_fetch_one_comment(http, k) for k in kids])
    return [c for c in fetched if c is not None]


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
    if not isinstance(data, dict):
        return []
    hits = data.get("hits")
    if not isinstance(hits, list):
        return []

    items: list[Item] = []
    now = utcnow()
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        title = hit.get("title") or ""
        if not isinstance(title, str):
            title = str(title)
        story_id = str(hit.get("objectID") or "")
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
        if not isinstance(url, str):
            url = str(url)
        if not title or not url:
            continue
        if not _matches_any_keyword(title, keywords):
            continue
        posted_at = parse_iso_utc(hit.get("created_at"), now)
        comments = (
            await _fetch_comments_for_story(http, story_id) if story_id else []
        )
        items.append(Item(
            title=title,
            url=url,
            canonical_url=canonical_url(url),
            source="hn",
            source_handle="front_page",
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=str(hit.get("author") or ""),
            excerpt="",
            language="en",
            metadata={
                "id": story_id,
                "created_at": str(hit.get("created_at") or ""),
                "content_kind": "title",
            },
            engagement={
                "score": int(hit.get("points") or 0),
                "comments": int(hit.get("num_comments") or 0),
            },
            comments=comments,
        ))
    return items
