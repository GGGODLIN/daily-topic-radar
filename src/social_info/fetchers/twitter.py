"""X / Twitter fetcher via Apify ScrapeBadger.

每個 handle 各打一發：這個 actor 只有一條 query，把多個 handle OR 在一起時，
吵的帳號會吃掉其他帳號的 max_results。source 之間本來就並行，這裡不再加佇列。

不打第二種 shape，HTTP 失敗也不重送。maxTotalChargeUsd 是 Apify run 的
query 參數，不是 actor input；它只限制這次 run 的 actor 計費，
不保證平台額外費也被這個數字封頂。
"""
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx

from social_info._time import utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.fetchers.relay import apify_post_url
from social_info.url_utils import canonical_url

ACTOR_ID = "scrape.badger~twitter-tweets-scraper"
API_URL = f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items"

# 現行最大 per_handle_limit 50 * $0.00015 = $0.0075，cap 留一點餘裕。
# 這個數字不是平台附加費的上限。
_MAX_TOTAL_CHARGE_USD = 0.01


def _format_window(window_hours: int) -> tuple[str, str]:
    now = utcnow()
    since = (now - timedelta(hours=window_hours)).strftime("%Y-%m-%d_%H:%M:%S_UTC")
    until = now.strftime("%Y-%m-%d_%H:%M:%S_UTC")
    return since, until


def _parse_tweet_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    dt: datetime | None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        dt = None
    if dt is None:
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def _handle_payload(
    handle: str, per_handle_limit: int, since: str, until: str
) -> dict:
    # actor 下限是 1。10 維持 10，不要把 Kaito 的 20 筆底數加回來。
    limit = int(per_handle_limit)
    if limit < 1:
        limit = 1
    return {
        "mode": "Advanced Search",
        "query": f"from:{handle} since:{since} until:{until}",
        "query_type": "Latest",
        "max_results": limit,
    }


def _count_mocks(data: list) -> int:
    return sum(
        1 for tw in data
        if isinstance(tw, dict) and tw.get("type") == "mock_tweet"
    )


def _tweet_url(tw: dict) -> str:
    username = str(tw.get("username") or "").strip().lstrip("@")
    tweet_id = str(tw.get("id") or "").strip()
    if not username or not tweet_id or tweet_id.startswith("-"):
        return ""
    return f"https://x.com/{username}/status/{tweet_id}"


def _to_items(data: list, source: SourceConfig, now: datetime) -> list[Item]:
    items: list[Item] = []
    for tw in data:
        # 新 actor 不帶 type=tweet。mock / 缺 id 或 username 仍丟，其餘留下。
        if not isinstance(tw, dict) or tw.get("type") == "mock_tweet":
            continue
        text = (tw.get("text") or "").strip()
        tweet_url = _tweet_url(tw)
        if not text or not tweet_url:
            continue
        username = str(tw.get("username") or "").strip().lstrip("@")
        posted_at = _parse_tweet_time(tw.get("created_at")) or now
        author = str(tw.get("user_name") or username).strip()
        items.append(Item(
            title=text[:120] + ("…" if len(text) > 120 else ""),
            url=tweet_url,
            canonical_url=canonical_url(tweet_url),
            source="x",
            source_handle=f"@{username}" if username else "",
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=author,
            excerpt=text[:200],
            language="en",
            engagement={
                "likes": int(tw.get("favorite_count") or 0),
                "comments": int(tw.get("reply_count") or 0),
                "retweets": int(tw.get("retweet_count") or 0),
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
    # relay 模式 params 是 None（token 不進 URL）；cap 仍要帶上。
    query = dict(params or {})
    query["maxTotalChargeUsd"] = _MAX_TOTAL_CHARGE_USD

    handles = source.params.get("handles", [])
    per_handle_limit = source.params.get("per_handle_limit", 10)
    window_hours = source.params.get("time_window_hours", 36)

    since, until = _format_window(window_hours)
    now = utcnow()

    items: list[Item] = []
    seen: set[str] = set()
    invalid_rows = 0
    mock_rows = 0
    saw_rows = False

    for handle in handles:
        payload = _handle_payload(handle, per_handle_limit, since, until)
        data = await _post(http, url, query, payload)
        if not data:
            continue
        saw_rows = True
        mock_rows += _count_mocks(data)
        batch = _to_items(data, source, now)
        invalid_rows += len(data) - len(batch)
        for item in batch:
            # 只避免同一個 source 輸出兩筆相同 URL。前一發已經計費，這裡退不了。
            key = item.canonical_url or item.url
            if key in seen:
                continue
            seen.add(key)
            items.append(item)

    if items:
        return items
    if saw_rows:
        raise RuntimeError(
            f"actor returned {invalid_rows} records but 0 usable tweets "
            f"({mock_rows} mock_tweet) — no usable row for {len(handles)} handles "
            f"over {window_hours}h; not retrying another query shape"
        )
    return []
