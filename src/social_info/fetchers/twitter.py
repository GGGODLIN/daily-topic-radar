"""X / Twitter fetcher via Apify Tweet Scraper actor (kaitoeasyapi).

Why Apify (vs twitterapi.io / scrape) — see BACKLOG.md and design spec:
- $0.25/1K tweets * 6K tweets/month = $1.50/month
- Apify free plan auto-refills $5 platform credits each month -> $0 actual cost
- Pay-Per Result, no rate limits, no personal-account ban risk
- Actor maintainer keeps anti-scrape working (no DevX maintenance)

Two request shapes, tried in order (2026-09-09):
1. `searchTerms` — one independent search per handle. `maxItems` is the cap
   *per search term*, so every handle gets its own budget.
2. `twitterContent` — a single OR-joined query. `maxItems` is a *global* cap
   ordered by recency, so it is scaled by handle count; quiet handles can still
   be crowded out by chatty ones, which is why this is the fallback and not the
   primary.

The actor ignores `twitterContent` whenever `searchTerms` is set, so the two
cannot be sent together — the fallback has to be a second request. Both fields
accept identical Twitter search syntax and bill identically (per returned
dataset item), so switching changes coverage shape only, never the rate.

Occurrences of the all-mock failure this fallback covers: 2026-08-20,
2026-09-09 (both recovered on their own within hours).
"""
from datetime import datetime, timedelta

import httpx

from social_info._time import utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.fetchers.relay import apify_post_url
from social_info.url_utils import canonical_url

ACTOR_ID = "kaitoeasyapi~twitter-x-data-tweet-scraper-pay-per-result-cheapest"
API_URL = f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items"

_TWITTER_TIME_FMT = "%a %b %d %H:%M:%S %z %Y"
_MIN_MAX_ITEMS = 20


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


def _search_terms_payload(
    handles: list[str], per_handle_limit: int, since: str, until: str
) -> dict:
    return {
        "searchTerms": [f"from:{h} since:{since} until:{until}" for h in handles],
        "maxItems": max(_MIN_MAX_ITEMS, per_handle_limit),
        "queryType": "Latest",
    }


def _twitter_content_payload(
    handles: list[str], per_handle_limit: int, since: str, until: str
) -> dict:
    joined = " OR ".join(f"from:{h}" for h in handles)
    return {
        "twitterContent": f"({joined}) since:{since} until:{until}",
        "maxItems": max(_MIN_MAX_ITEMS, per_handle_limit * len(handles)),
        "queryType": "Latest",
    }


def _count_mocks(data: list) -> int:
    return sum(
        1 for tw in data
        if isinstance(tw, dict) and tw.get("type") == "mock_tweet"
    )


def _to_items(data: list, source: SourceConfig, now: datetime) -> list[Item]:
    items: list[Item] = []
    for tw in data:
        if not isinstance(tw, dict) or tw.get("type") != "tweet":
            continue
        text = (tw.get("text") or "").strip()
        tweet_url = tw.get("url") or ""
        if not text or not tweet_url:
            continue
        author = tw.get("author") or {}
        username = author.get("userName") or ""
        posted_at = _parse_tweet_time(tw.get("createdAt", "")) or now
        items.append(Item(
            title=text[:120] + ("…" if len(text) > 120 else ""),
            url=tweet_url,
            canonical_url=canonical_url(tweet_url),
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


async def _post(
    http: httpx.AsyncClient, url: str, params: dict | None, payload: dict
) -> list:
    resp = await http.post(url, params=params, json=payload, timeout=180.0)
    resp.raise_for_status()
    return resp.json()


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    url, params = apify_post_url(API_URL, "APIFY_TOKEN_TWITTER")

    handles = source.params.get("handles", [])
    per_handle_limit = source.params.get("per_handle_limit", 10)
    window_hours = source.params.get("time_window_hours", 36)

    since, until = _format_window(window_hours)
    now = utcnow()

    builders = (_search_terms_payload, _twitter_content_payload)
    attempted_fields: list[str] = []
    last_data: list = []

    for build in builders:
        payload = build(handles, per_handle_limit, since, until)
        attempted_fields.append(next(iter(payload)))
        data = await _post(http, url, params, payload)
        if not data:
            return []
        items = _to_items(data, source, now)
        if items:
            return items
        last_data = data

    raise RuntimeError(
        f"actor returned {len(last_data)} records but 0 usable tweets "
        f"({_count_mocks(last_data)} mock_tweet padding) — upstream X search "
        f"found nothing for {len(handles)} handles over {window_hours}h "
        f"across {len(attempted_fields)} request shapes "
        f"({', '.join(attempted_fields)})"
    )
