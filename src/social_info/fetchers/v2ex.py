"""V2EX fetcher via public API (hot topics + node topics)."""
import re

import httpx

from social_info._time import safe_utcfromtimestamp, utcnow
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


def _safe_int(value: object) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return 0


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
        if not isinstance(topic, dict):
            continue
        raw_title = topic.get("title") or ""
        title = raw_title if isinstance(raw_title, str) else str(raw_title)
        topic_id = topic.get("id") or 0
        raw_url = topic.get("url")
        url = raw_url if isinstance(raw_url, str) and raw_url else f"https://www.v2ex.com/t/{topic_id}"
        if not title:
            continue
        raw_content = topic.get("content") or ""
        content = raw_content if isinstance(raw_content, str) else str(raw_content)
        if not _matches_any_keyword(f"{title} {content}", keywords):
            continue
        raw_node = topic.get("node")
        node_obj = raw_node if isinstance(raw_node, dict) else {}
        raw_member = topic.get("member")
        member = raw_member if isinstance(raw_member, dict) else {}
        created_ts = topic.get("created")
        posted_at = safe_utcfromtimestamp(created_ts, now) if created_ts else now
        source_handle = str(node_obj.get("name") or node)
        items.append(Item(
            title=title,
            url=url,
            canonical_url=canonical_url(url),
            source="v2ex",
            source_handle=source_handle,
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=str(member.get("username") or ""),
            excerpt=_clean(content),
            language="zh-CN",
            engagement={"comments": _safe_int(topic.get("replies"))},
            metadata={
                "id": str(topic_id),
                "created_at": str(topic.get("created") or ""),
                "content_kind": "post_text" if content else "summary",
            },
            content=content,
        ))
    return items
