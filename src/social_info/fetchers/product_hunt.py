"""Product Hunt GraphQL fetcher.

Reads PRODUCT_HUNT_TOKEN from the environment and sends it as a Bearer header.
"""
import os

import httpx

from social_info._time import parse_iso_utc, utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

API_URL = "https://api.producthunt.com/v2/api/graphql"

QUERY = """
query DailyAITopProducts($topic: String!, $limit: Int!) {
  posts(topic: $topic, first: $limit, order: VOTES) {
    edges {
      node {
        id
        name
        tagline
        url
        votesCount
        commentsCount
        createdAt
        user { name }
      }
    }
  }
}
"""


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    token = os.environ.get("PRODUCT_HUNT_TOKEN")
    if not token:
        raise RuntimeError("PRODUCT_HUNT_TOKEN env var not set")

    topic = source.params.get("topic", "artificial-intelligence")
    limit = source.params.get("limit", 10)
    resp = await http.post(
        API_URL,
        json={"query": QUERY, "variables": {"topic": topic, "limit": limit}},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        return []
    root = data.get("data")
    posts = root.get("posts") if isinstance(root, dict) else None
    edges = posts.get("edges") if isinstance(posts, dict) else None
    if not isinstance(edges, list):
        return []

    items: list[Item] = []
    now = utcnow()
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        n = edge.get("node")
        if not isinstance(n, dict) or not n:
            continue
        name = n.get("name")
        url = n.get("url")
        if not isinstance(name, str) or not name or not isinstance(url, str) or not url:
            continue
        tagline = n.get("tagline") or ""
        if not isinstance(tagline, str):
            tagline = str(tagline)
        title = f"{name} — {tagline}".strip(" —")
        posted_at = parse_iso_utc(n.get("createdAt"), now)
        user = n.get("user")
        author = str(user.get("name") or "") if isinstance(user, dict) else ""
        try:
            votes = int(n.get("votesCount") or 0)
        except (TypeError, ValueError, OverflowError):
            votes = 0
        try:
            comments = int(n.get("commentsCount") or 0)
        except (TypeError, ValueError, OverflowError):
            comments = 0
        items.append(Item(
            title=title,
            url=url,
            canonical_url=canonical_url(url),
            source="product_hunt",
            source_handle="daily_top_ai",
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=author,
            excerpt=tagline[:200],
            language="en",
            metadata={
                "id": str(n.get("id") or ""),
                "created_at": str(n.get("createdAt") or ""),
                "content_kind": "tagline",
            },
            engagement={
                "votes": votes,
                "comments": comments,
            },
        ))
    return items
