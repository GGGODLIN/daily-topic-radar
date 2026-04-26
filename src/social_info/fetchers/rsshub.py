"""RSSHub fetcher: combine instance URL + path, parse as RSS."""
import os
import re
from datetime import datetime
from time import mktime

import feedparser
import httpx

from social_info._time import utcnow
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
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        if not title or not link:
            continue
        if entry.get("published_parsed"):
            posted_at = datetime.fromtimestamp(mktime(entry.published_parsed))
        else:
            posted_at = now
        raw_excerpt = entry.get("summary") or entry.get("description") or ""
        excerpt = _WS_RE.sub(" ", _TAG_RE.sub("", raw_excerpt)).strip()[:200]
        items.append(Item(
            title=title,
            url=link,
            canonical_url=canonical_url(link),
            source="rsshub",
            source_handle=source.id,
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=entry.get("author", "") or "",
            excerpt=excerpt,
            language=source.language,
            engagement={},
        ))
    return items
