"""Trendshift Rising Engagement scraper.

Trendshift (trendshift.io) ranks GitHub repos by daily engagement velocity
(stars + Reddit + HN signals) instead of absolute trending. The homepage
embeds the top-25 rising list in Next.js __next_f streaming chunks; we
parse the chunks directly without running JS.
"""
import json
import re

import httpx

from social_info._time import utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

URL = "https://trendshift.io"
_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[\d+,"(.*?)"\]\)', re.DOTALL)
_FULL_NAME_RE = re.compile(r'"full_name":"([^"]+)"')


def _decode_streaming_payload(html: str) -> str:
    chunks = _CHUNK_RE.findall(html)
    return "".join(c.encode().decode("unicode_escape") for c in chunks)


def _extract_balanced_object(s: str, anchor_idx: int) -> str | None:
    depth = 0
    i = anchor_idx
    obj_start = -1
    while i >= 0:
        c = s[i]
        if c == "}":
            depth += 1
        elif c == "{":
            if depth == 0:
                obj_start = i
                break
            depth -= 1
        i -= 1
    if obj_start < 0:
        return None
    depth = 1
    in_str = False
    esc = False
    i = obj_start + 1
    while i < len(s):
        c = s[i]
        if esc:
            esc = False
        elif in_str:
            if c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return s[obj_start : i + 1]
        i += 1
    return None


def _parse_repos(decoded: str) -> list[dict]:
    seen: set[tuple[int, str]] = set()
    out: list[dict] = []
    for m in _FULL_NAME_RE.finditer(decoded):
        obj_str = _extract_balanced_object(decoded, m.start())
        if not obj_str:
            continue
        try:
            d = json.loads(obj_str)
        except json.JSONDecodeError:
            continue
        if not all(k in d for k in ("rank", "score", "full_name")):
            continue
        key = (int(d["rank"]), d["full_name"])
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    out.sort(key=lambda x: int(x["rank"]))
    return out


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    limit = source.params.get("limit", 25)
    keywords = source.params.get("ai_keywords", [])

    resp = await http.get(URL, timeout=30.0)
    resp.raise_for_status()
    decoded = _decode_streaming_payload(resp.text)
    repos = _parse_repos(decoded)[:limit]

    items: list[Item] = []
    now = utcnow()
    for d in repos:
        full_name = d["full_name"]
        if keywords and not any(k.lower() in full_name.lower() for k in keywords):
            continue
        url = f"https://github.com/{full_name}"
        items.append(
            Item(
                title=full_name,
                url=url,
                canonical_url=canonical_url(url),
                source="trendshift",
                source_handle=f"rising:rank-{d['rank']}",
                source_tier=source.tier,
                posted_at=now,
                fetched_at=now,
                author=full_name.split("/")[0],
                excerpt=d.get("description", "")[:200],
                language="en",
                engagement={
                    "rank": int(d["rank"]),
                    "score": int(float(d["score"])),
                },
            )
        )
    return items
