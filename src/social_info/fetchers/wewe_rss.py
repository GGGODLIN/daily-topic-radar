"""WeChat Official Account fetcher via self-hosted wewe-rss instance.

DISABLED BY DEFAULT in sources.yml during the first month of PoC.
Once self-host is set up:
1. Run wewe-rss docker (see spec Appendix D)
2. Set WEWE_RSS_URL and WEWE_RSS_KEY env vars
3. Flip the wechat_* sources to enabled: true in sources.yml

The wewe-rss instance exposes per-account RSS feeds at
{WEWE_RSS_URL}/feeds/{account_id}.atom — we delegate parsing to
feedparser as with the generic RSS fetcher.
"""
import os
import re
from datetime import datetime
from time import mktime

import feedparser
import httpx

from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    base = os.environ.get("WEWE_RSS_URL")
    if not base:
        raise RuntimeError(
            "WEWE_RSS_URL env var not set (wewe-rss source enabled but not configured)"
        )
    key = os.environ.get("WEWE_RSS_KEY", "")
    account_id = source.params["account_id"]
    limit = source.params.get("limit", 20)

    feed_url = f"{base.rstrip('/')}/feeds/{account_id}.atom"
    resp = await http.get(
        feed_url,
        params={"key": key} if key else None,
        timeout=30.0,
    )
    resp.raise_for_status()
    parsed = feedparser.parse(resp.text)

    items: list[Item] = []
    now = datetime.utcnow()
    for entry in parsed.entries[:limit]:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        if not title or not link:
            continue
        if entry.get("published_parsed"):
            posted_at = datetime.fromtimestamp(mktime(entry.published_parsed))
        else:
            posted_at = now
        raw = entry.get("summary") or ""
        excerpt = _WS_RE.sub(" ", _TAG_RE.sub("", raw)).strip()[:200]
        items.append(Item(
            title=title,
            url=link,
            canonical_url=canonical_url(link),
            source="wewe_rss",
            source_handle=account_id,
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=entry.get("author", "") or "",
            excerpt=excerpt,
            language=source.language,
            engagement={},
        ))
    return items
