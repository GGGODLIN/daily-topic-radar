"""Generic RSS / Atom fetcher (lab blogs, tech media, Taiwan media)."""
import html as html_lib

import feedparser
import httpx
from bs4 import BeautifulSoup

from social_info._time import struct_time_to_utc, utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url


def _excerpt(html: str) -> str:
    # Existing summary path stays a collapsed 200-char excerpt.
    text = html_lib.unescape(BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True))
    return " ".join(text.split())[:200]


def _feed_content(entry) -> tuple[str, str]:
    """Only the feed content field. Summary stays the excerpt and is not body text."""
    content = entry.get("content")
    if not isinstance(content, list) or not content or not isinstance(content[0], dict):
        return "", ""
    raw = content[0].get("value") or ""
    if not isinstance(raw, str):
        raw = str(raw)
    return html_lib.unescape(BeautifulSoup(raw, "html.parser").get_text("\n", strip=True)), "feed_content"


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    url = source.params["url"]
    limit = source.params.get("limit", 30)
    user_agent = source.params.get("user_agent")
    headers = {"User-Agent": str(user_agent)} if user_agent else None
    resp = await http.get(url, headers=headers, timeout=30.0)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.text)

    items: list[Item] = []
    now = utcnow()
    for entry in parsed.entries[:limit]:
        raw_title = entry.get("title")
        title = html_lib.unescape(raw_title).strip() if isinstance(raw_title, str) else ""
        raw_link = entry.get("link")
        link = raw_link.strip() if isinstance(raw_link, str) else ""
        if not title or not link:
            continue
        posted_at = struct_time_to_utc(entry.get("published_parsed"), None)
        if posted_at is None:
            posted_at = struct_time_to_utc(entry.get("updated_parsed"), now)
        raw_summary = entry.get("summary") or entry.get("description") or ""
        excerpt = _excerpt(raw_summary if isinstance(raw_summary, str) else str(raw_summary))
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
            author=str(entry.get("author") or ""),
            excerpt=excerpt,
            language=source.language,
            engagement={},
            content=body,
            metadata={"content_kind": kind} if kind else {},
        ))
    return items
