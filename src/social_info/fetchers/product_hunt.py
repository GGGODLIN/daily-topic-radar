"""Product Hunt GraphQL fetcher.

Requires a Bearer token (PRODUCT_HUNT_TOKEN env var) which the orchestrator
attaches to the httpx.AsyncClient headers before calling this fetcher.
"""
import os
from datetime import datetime

import httpx

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
    edges = data.get("data", {}).get("posts", {}).get("edges", [])

    items: list[Item] = []
    now = datetime.utcnow()
    for edge in edges:
        n = edge.get("node", {})
        if not n:
            continue
        title = f"{n['name']} — {n.get('tagline', '')}".strip(" —")
        url = n["url"]
        try:
            posted_at = datetime.fromisoformat(
                n["createdAt"].replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except (KeyError, ValueError):
            posted_at = now
        items.append(Item(
            title=title,
            url=url,
            canonical_url=canonical_url(url),
            source="product_hunt",
            source_handle="daily_top_ai",
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=(n.get("user") or {}).get("name", ""),
            excerpt=n.get("tagline", "")[:200],
            language="en",
            engagement={
                "votes": int(n.get("votesCount") or 0),
                "comments": int(n.get("commentsCount") or 0),
            },
        ))
    return items
