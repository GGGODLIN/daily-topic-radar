"""V2EX fetcher via public API (hot topics + node topics)."""
import re

import httpx

from social_info._time import utcfromtimestamp, utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

HOT_URL = "https://www.v2ex.com/api/topics/hot.json"
NODE_URL = "https://www.v2ex.com/api/topics/show.json"
UA = "social-info/1.0"
EXCERPT_MAX = 200

_WS_RE = re.compile(r"\s+")


def _clean(text: str) -> str:
    collapsed = _WS_RE.sub(" ", text or "").strip()
    if len(collapsed) > EXCERPT_MAX:
        return collapsed[:EXCERPT_MAX] + "…"
    return collapsed


def _matches_any_keyword(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    lower = text.lower()
    return any(k.lower() in lower for k in keywords)


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    node = (source.params.get("node") or "hot").strip()
    limit = source.params.get("limit", 20)
    keywords = source.params.get("keywords", [])

    if node == "hot":
        resp = await http.get(HOT_URL, headers={"User-Agent": UA}, timeout=30.0)
    else:
        resp = await http.get(
            NODE_URL,
            params={"node_name": node, "page": 1},
            headers={"User-Agent": UA},
            timeout=30.0,
        )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        return []

    items: list[Item] = []
    now = utcnow()
    for topic in data[:limit]:
        title = topic.get("title") or ""
        topic_id = topic.get("id") or 0
        url = topic.get("url") or f"https://www.v2ex.com/t/{topic_id}"
        if not title:
            continue
        content = topic.get("content") or ""
        if not _matches_any_keyword(f"{title} {content}", keywords):
            continue
        node_obj = topic.get("node") or {}
        member = topic.get("member") or {}
        try:
            created_ts = int(topic.get("created") or 0)
        except (TypeError, ValueError):
            created_ts = 0
        posted_at = utcfromtimestamp(created_ts) if created_ts else now
        items.append(Item(
            title=title,
            url=url,
            canonical_url=canonical_url(url),
            source="v2ex",
            source_handle=node_obj.get("name", node),
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=member.get("username", ""),
            excerpt=_clean(content),
            language="zh-CN",
            engagement={"comments": int(topic.get("replies") or 0)},
        ))
    return items
