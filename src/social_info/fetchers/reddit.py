"""Reddit fetcher via public top.json endpoint."""
from datetime import datetime

import httpx

from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

USER_AGENT = "social-info/0.1 (daily AI raw aggregator; personal use)"


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    subreddit = source.params["subreddit"]
    time_window = source.params.get("time_window", "day")
    limit = source.params.get("limit", 10)

    url = f"https://www.reddit.com/r/{subreddit}/top.json"
    resp = await http.get(
        url,
        params={"t": time_window, "limit": limit},
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()

    items: list[Item] = []
    now = datetime.utcnow()
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        title = post.get("title") or ""
        link = post.get("url") or f"https://www.reddit.com{post.get('permalink', '')}"
        if not title or not link:
            continue
        if post.get("is_self") or post.get("post_hint") == "image":
            link = f"https://www.reddit.com{post.get('permalink', '')}"
        excerpt = (post.get("selftext") or "").strip()[:200]
        try:
            posted_at = datetime.utcfromtimestamp(post.get("created_utc", 0))
        except (TypeError, ValueError):
            posted_at = now
        items.append(Item(
            title=title,
            url=link,
            canonical_url=canonical_url(link),
            source="reddit",
            source_handle=f"r/{subreddit}",
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=post.get("author") or "",
            excerpt=excerpt,
            language="en",
            engagement={
                "score": int(post.get("score") or 0),
                "comments": int(post.get("num_comments") or 0),
            },
        ))
    return items
