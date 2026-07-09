"""Reddit fetcher via old.reddit.com listing HTML.

www.reddit.com `.json` endpoints are blocked for unauthenticated clients
(HTTP 403 "blocked by network security"), independent of VPN/IP — a
residential IP reaches the HTML pages (200) but not the JSON API. old.reddit
listing HTML stays publicly reachable and carries score / permalink / author /
comment-count / timestamp as data-* attributes on each `div.thing`.
"""
import httpx
from bs4 import BeautifulSoup

from social_info._time import utcfromtimestamp, utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

USER_AGENT = "social-info/0.1 (daily AI raw aggregator; personal use)"

_REDDIT_INTERNAL_PREFIXES = (
    "https://www.reddit.com",
    "https://reddit.com",
    "https://old.reddit.com",
    "https://i.redd.it",
    "https://v.redd.it",
)


def _int_attr(thing, name: str) -> int:
    raw = (thing.get(name) or "").strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _parse_things(html: str, subreddit: str, limit: int) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    parsed: list[dict] = []
    for thing in soup.find_all("div", class_="thing"):
        if (thing.get("data-promoted") or "").strip() == "true":
            continue
        permalink = (thing.get("data-permalink") or "").strip()
        if not permalink:
            continue
        title_el = thing.find("a", class_="title")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue
        parsed.append({
            "title": title,
            "permalink": permalink,
            "outbound": (thing.get("data-url") or "").strip(),
            "author": (thing.get("data-author") or "").strip(),
            "score": _int_attr(thing, "data-score"),
            "comments": _int_attr(thing, "data-comments-count"),
            "timestamp_ms": _int_attr(thing, "data-timestamp"),
        })
        if len(parsed) >= limit:
            break
    return parsed


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    subreddit = source.params["subreddit"]
    time_window = source.params.get("time_window", "day")
    limit = source.params.get("limit", 10)

    url = f"https://old.reddit.com/r/{subreddit}/top/"
    resp = await http.get(
        url,
        params={"t": time_window, "limit": limit},
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
    )
    resp.raise_for_status()

    items: list[Item] = []
    now = utcnow()
    for post in _parse_things(resp.text, subreddit, limit):
        link = f"https://www.reddit.com{post['permalink']}"
        outbound = post["outbound"]
        is_external_link = bool(outbound) and not outbound.startswith(_REDDIT_INTERNAL_PREFIXES) and not outbound.startswith("/")
        excerpt = f"src [{outbound}]({outbound})" if is_external_link else ""
        if post["timestamp_ms"]:
            try:
                posted_at = utcfromtimestamp(post["timestamp_ms"] / 1000)
            except (TypeError, ValueError, OverflowError):
                posted_at = now
        else:
            posted_at = now
        items.append(Item(
            title=post["title"],
            url=link,
            canonical_url=canonical_url(link),
            source="reddit",
            source_handle=f"r/{subreddit}",
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=post["author"],
            excerpt=excerpt,
            language="en",
            engagement={
                "score": post["score"],
                "comments": post["comments"],
            },
        ))
    return items
