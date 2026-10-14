"""Generic RSS / Atom fetcher (lab blogs, tech media, Taiwan media)."""
from datetime import datetime
from time import mktime

import feedparser
import httpx
from bs4 import BeautifulSoup

from social_info._time import utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url


def _excerpt(html: str) -> str:
    # Existing summary path stays a collapsed 200-char excerpt.
    text = BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)
    return " ".join(text.split())[:200]


def _feed_content(entry) -> tuple[str, str]:
    """Only the feed content field. Summary stays the excerpt and is not body text."""
    content = entry.get("content")
    if not content:
        return "", ""
    raw = content[0].get("value", "") or ""
    return BeautifulSoup(raw, "html.parser").get_text("\n", strip=True), "feed_content"


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    url = source.params["url"]
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
        elif entry.get("updated_parsed"):
            posted_at = datetime.fromtimestamp(mktime(entry.updated_parsed))
        else:
            posted_at = now
        excerpt = _excerpt(entry.get("summary") or entry.get("description") or "")
        body, kind = _feed_content(entry)

        items.append(Item(
            title=title,
            url=link,
            canonical_url=canonical_url(link),
            source="rss",
            source_handle=source.id,
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=entry.get("author", "") or "",
            excerpt=excerpt,
            language=source.language,
            engagement={},
            content=body,
            metadata={"content_kind": kind} if kind else {},
        ))
    return items
