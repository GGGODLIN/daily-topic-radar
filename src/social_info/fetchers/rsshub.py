"""RSSHub fetcher: combine instance URL + path, parse as RSS."""
import html
import os
import re

import feedparser
import httpx

from social_info._time import struct_time_to_utc, utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _instance() -> str:
    return os.environ.get("RSSHUB_INSTANCE_URL", "https://rsshub.app").rstrip("/")


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    path = source.params["path"]
    if not path.startswith("/"):
        path = "/" + path
    url = _instance() + path
    limit = source.params.get("limit", 30)

    resp = await http.get(url, timeout=30.0)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.text)

    items: list[Item] = []
    now = utcnow()
    for entry in parsed.entries[:limit]:
        raw_title = entry.get("title")
        title = html.unescape(raw_title).strip() if isinstance(raw_title, str) else ""
        raw_link = entry.get("link")
        link = raw_link.strip() if isinstance(raw_link, str) else ""
        if not title or not link:
            continue
        posted_at = struct_time_to_utc(entry.get("published_parsed"), None)
        if posted_at is None:
            posted_at = struct_time_to_utc(entry.get("updated_parsed"), now)
        raw_excerpt = entry.get("summary") or entry.get("description") or ""
        if not isinstance(raw_excerpt, str):
            raw_excerpt = str(raw_excerpt)
        excerpt = _WS_RE.sub(" ", html.unescape(_TAG_RE.sub("", raw_excerpt))).strip()[:200]
        items.append(Item(
            title=title,
            url=link,
            canonical_url=canonical_url(link),
            source="rsshub",
            source_handle=source.id,
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=str(entry.get("author") or ""),
            excerpt=excerpt,
            language=source.language,
            engagement={},
            metadata={"content_kind": "summary"},
        ))
    return items
