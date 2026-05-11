"""Hacker News fetcher via Algolia API, comments enrichment via Firebase API."""
import asyncio
import html
import re
from datetime import datetime, timedelta

import httpx

from social_info._time import utcfromtimestamp, utcnow
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
        data = resp.json() or {}
    except Exception:
        return None
    if data.get("deleted") or data.get("dead"):
        return None
    by = data.get("by") or ""
    text = _clean_comment_text(data.get("text") or "")
    if not by or not text:
        return None
    try:
        ts_int = int(data.get("time")) if data.get("time") else None
    except (TypeError, ValueError):
        ts_int = None
    posted_at = (
        utcfromtimestamp(ts_int).replace(microsecond=0).isoformat()
        if ts_int
        else ""
    )
    return {"author": by, "text": text, "posted_at": posted_at}


async def _fetch_comments_for_story(
    http: httpx.AsyncClient, story_id: str
) -> list[dict]:
    try:
        resp = await http.get(FIREBASE_URL.format(id=story_id), timeout=15.0)
        resp.raise_for_status()
        story = resp.json() or {}
    except Exception:
        return []
    kids = (story.get("kids") or [])[:MAX_COMMENTS]
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

    items: list[Item] = []
    now = utcnow()
    for hit in data.get("hits", []):
        title = hit.get("title") or ""
        story_id = hit.get("objectID") or ""
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
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
            author=hit.get("author") or "",
            excerpt="",
            language="en",
            engagement={
                "score": int(hit.get("points") or 0),
                "comments": int(hit.get("num_comments") or 0),
            },
            comments=comments,
        ))
    return items
