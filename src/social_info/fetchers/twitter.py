"""X / Twitter fetcher via twitterapi.io."""
import os
from datetime import datetime, timedelta

import httpx

from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

API_BASE = "https://api.twitterapi.io"


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    api_key = os.environ.get("TWITTERAPI_IO_KEY")
    if not api_key:
        raise RuntimeError("TWITTERAPI_IO_KEY env var not set")

    handles = source.params.get("handles", [])
    per_handle_limit = source.params.get("per_handle_limit", 10)
    window_hours = source.params.get("time_window_hours", 24)
    since_iso = (datetime.utcnow() - timedelta(hours=window_hours)).isoformat() + "Z"

    items: list[Item] = []
    now = datetime.utcnow()
    headers = {"X-API-Key": api_key}

    for handle in handles:
        resp = await http.get(
            f"{API_BASE}/twitter/user/last_tweets",
            params={
                "userName": handle,
                "limit": per_handle_limit,
                "sinceTime": since_iso,
            },
            headers=headers,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        for tw in data.get("tweets", []):
            text = tw.get("text", "").strip()
            url = tw.get("url") or f"https://twitter.com/{handle}/status/{tw['id']}"
            try:
                posted_at = datetime.fromisoformat(
                    tw["createdAt"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except (KeyError, ValueError):
                posted_at = now
            items.append(Item(
                title=text[:120] + ("…" if len(text) > 120 else ""),
                url=url,
                canonical_url=canonical_url(url),
                source="x",
                source_handle=f"@{handle}",
                source_tier=source.tier,
                posted_at=posted_at,
                fetched_at=now,
                author=(tw.get("author") or {}).get("name", "") or handle,
                excerpt=text[:200],
                language="en",
                engagement={
                    "likes": int(tw.get("likeCount") or 0),
                    "comments": int(tw.get("replyCount") or 0),
                    "retweets": int(tw.get("retweetCount") or 0),
                },
            ))
    return items
