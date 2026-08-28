import sys
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from social_info._time import utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

BASE_URL = "https://skills.sh"
BOARDS = ("trending", "hot")
USER_AGENT = "social-info/0.1 (daily AI raw aggregator; personal use)"
_MULTIPLIERS = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


def _parse_compact_count(value: str) -> int:
    cleaned = value.strip().replace(",", "")
    suffix = cleaned[-1:].upper()
    multiplier = _MULTIPLIERS.get(suffix, 1)
    number = cleaned[:-1] if multiplier != 1 else cleaned
    try:
        return int(Decimal(number) * multiplier)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid compact count: {value!r}") from exc


def _parse_board(html: str, board: str, tier: int) -> list[Item]:
    soup = BeautifulSoup(html, "html.parser")
    now = utcnow()
    items: list[Item] = []
    for link in soup.select("a[href]"):
        name_el = link.find("h3")
        source_el = link.find("p")
        spans = link.find_all("span")
        if name_el is None or source_el is None or len(spans) < 2:
            continue
        rank_text = spans[0].get_text(strip=True)
        if not rank_text.isdigit():
            continue
        try:
            installs = _parse_compact_count(spans[-1].get_text(strip=True))
        except ValueError:
            continue
        href = link.get("href", "")
        name = name_el.get_text(" ", strip=True)
        publisher = source_el.get_text(" ", strip=True)
        if not href or not name or not publisher:
            continue
        rank = int(rank_text)
        url = urljoin(BASE_URL, href)
        items.append(
            Item(
                title=name,
                url=url,
                canonical_url=canonical_url(url),
                source="skills_sh",
                source_handle=f"{board}:rank-{rank}",
                source_tier=tier,
                posted_at=now,
                fetched_at=now,
                author=publisher,
                language="en",
                engagement={"rank": rank, "installs": installs},
            )
        )
    return items


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    limit = source.params.get("limit", 25)
    items: list[Item] = []
    last_exc: Exception | None = None
    for board in BOARDS:
        try:
            resp = await http.get(
                f"{BASE_URL}/{board}",
                headers={"User-Agent": USER_AGENT},
                timeout=30.0,
            )
            resp.raise_for_status()
        except Exception as exc:
            last_exc = exc
            print(f"skills_sh {board} failed: {exc}", file=sys.stderr)
            continue
        items.extend(_parse_board(resp.text, board, source.tier)[:limit])
    if not items and last_exc is not None:
        raise last_exc

    merged: list[Item] = []
    items_by_url: dict[str, Item] = {}
    for item in items:
        prior = items_by_url.get(item.canonical_url)
        if prior is None:
            merged.append(item)
            items_by_url[item.canonical_url] = item
            continue
        prior.also_appeared_in.append({
            "source": item.source,
            "source_handle": item.source_handle,
            "url": item.url,
        })
    return merged
