"""X / Twitter fetcher via Apify Tweet Scraper actor (kaitoeasyapi).

Why Apify (vs twitterapi.io / scrape) — see BACKLOG.md and design spec:
- $0.25/1K tweets * 6K tweets/month = $1.50/month
- Apify free plan auto-refills $5 platform credits each month -> $0 actual cost
- Pay-Per Result, no rate limits, no personal-account ban risk
- Actor maintainer keeps anti-scrape working (no DevX maintenance)
"""
import os
from datetime import datetime, timedelta

import httpx

from social_info._time import utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

ACTOR_ID = "kaitoeasyapi~twitter-x-data-tweet-scraper-pay-per-result-cheapest"
API_URL = f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items"

_TWITTER_TIME_FMT = "%a %b %d %H:%M:%S %z %Y"


def _format_window(window_hours: int) -> tuple[str, str]:
    now = utcnow()
    since = (now - timedelta(hours=window_hours)).strftime("%Y-%m-%d_%H:%M:%S_UTC")
    until = now.strftime("%Y-%m-%d_%H:%M:%S_UTC")
    return since, until


def _parse_tweet_time(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, _TWITTER_TIME_FMT).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    token = os.environ.get("APIFY_TOKEN_TWITTER")
    if not token:
        raise RuntimeError("APIFY_TOKEN_TWITTER env var not set")

    handles = source.params.get("handles", [])
    per_handle_limit = source.params.get("per_handle_limit", 10)
    window_hours = source.params.get("time_window_hours", 24)

    since, until = _format_window(window_hours)
    search_terms = [f"from:{h} since:{since} until:{until}" for h in handles]

    payload = {
        "searchTerms": search_terms,
        "maxItems": max(20, per_handle_limit),
        "queryType": "Latest",
    }

    resp = await http.post(
        API_URL,
        params={"token": token},
        json=payload,
        timeout=180.0,
    )
    resp.raise_for_status()
    data = resp.json()

    items: list[Item] = []
    now = utcnow()
    for tw in data:
        if tw.get("type") != "tweet":
            continue
        text = (tw.get("text") or "").strip()
        url = tw.get("url") or ""
        if not text or not url:
            continue
        author = tw.get("author") or {}
        username = author.get("userName") or ""
        posted_at = _parse_tweet_time(tw.get("createdAt", "")) or now
        items.append(Item(
            title=text[:120] + ("…" if len(text) > 120 else ""),
            url=url,
            canonical_url=canonical_url(url),
            source="x",
            source_handle=f"@{username}" if username else "",
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=author.get("name") or username,
            excerpt=text[:200],
            language="en",
            engagement={
                "likes": int(tw.get("likeCount") or 0),
                "comments": int(tw.get("replyCount") or 0),
                "retweets": int(tw.get("retweetCount") or 0),
            },
        ))
    return items
