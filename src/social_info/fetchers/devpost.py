"""Devpost public hackathons JSON API fetcher."""
import html
import re
from datetime import datetime

import httpx

from social_info._time import utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

API_URL = "https://devpost.com/api/hackathons"
USER_AGENT = "social-info/0.1 (daily AI raw aggregator; personal use)"
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_html(raw: object) -> str:
    text = html.unescape(_TAG_RE.sub("", str(raw or "")))
    return _WS_RE.sub(" ", text).strip()


def _to_item(entry: dict, tier: int, now: datetime, min_cash_prizes: int) -> Item | None:
    if entry.get("invite_only"):
        return None
    title = _clean_html(entry.get("title"))
    url = str(entry.get("url") or "").strip()
    if not title or not url:
        return None

    prizes_counts = entry.get("prizes_counts") or {}
    cash_count = int(prizes_counts.get("cash") or 0)
    other_count = int(prizes_counts.get("other") or 0)
    if cash_count < min_cash_prizes:
        return None

    org = _clean_html(entry.get("organization_name")) or "devpost"
    prize = _clean_html(entry.get("prize_amount")) or "$0"
    state = _clean_html(entry.get("open_state")) or "open"
    time_left = _clean_html(entry.get("time_left_to_submission"))
    dates = _clean_html(entry.get("submission_period_dates"))
    reg = int(entry.get("registrations_count") or 0)
    themes = [
        _clean_html(t.get("name"))
        for t in (entry.get("themes") or [])
        if isinstance(t, dict) and t.get("name")
    ]
    resources_url = f"{url.rstrip('/')}/resources"
    rules_url = f"{url.rstrip('/')}/rules"

    parts = [
        f"Org: {org}",
        f"Prize: {prize} (cash_prizes: {cash_count}, other: {other_count})",
        f"State: {state}",
    ]
    if time_left or dates:
        timing = " / ".join(x for x in (time_left, dates) if x)
        parts.append(f"Window: {timing}")
    parts.append(f"Registered: {reg}")
    if themes:
        parts.append(f"Themes: {', '.join(themes)}")
    parts.append(f"Resources: {resources_url}")
    parts.append(f"Rules: {rules_url}")
    excerpt = " | ".join(parts)

    return Item(
        title=title,
        url=url,
        canonical_url=canonical_url(url),
        source="devpost",
        source_handle=org,
        source_tier=tier,
        posted_at=now,
        fetched_at=now,
        author=org,
        excerpt=excerpt,
        language="en",
        engagement={"registrations": reg, "cash_prizes": cash_count},
        content=excerpt,
        metadata={"content_kind": "summary"},
    )


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    limit = int(source.params.get("limit", 25))
    min_cash_prizes = int(source.params.get("min_cash_prizes", 1))
    themes = source.params.get("themes") or ["Machine Learning/AI"]
    statuses = source.params.get("statuses") or ["open", "upcoming"]
    order_by = str(source.params.get("order_by") or "prize-amount")
    now = utcnow()

    query_params: list[tuple[str, str]] = [
        ("challenge_type[]", "online"),
        ("order_by", order_by),
        ("per_page", str(max(limit, 30))),
    ]
    for st in statuses:
        query_params.append(("status[]", str(st)))
    for th in themes:
        query_params.append(("themes[]", str(th)))

    resp = await http.get(
        API_URL,
        params=query_params,
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
    )
    resp.raise_for_status()
    payload = resp.json() or {}
    entries = payload.get("hackathons") or []

    items: list[Item] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        item = _to_item(entry, source.tier, now, min_cash_prizes)
        if item is not None:
            items.append(item)
        if len(items) >= limit:
            break
    return items
