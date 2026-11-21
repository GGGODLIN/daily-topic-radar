"""Trendshift Rising Engagement scraper.

Trendshift (trendshift.io) ranks GitHub repos by daily engagement velocity
(stars + Reddit + HN signals) instead of absolute trending. The homepage
embeds the top-25 rising list in Next.js __next_f streaming chunks; we
parse the chunks directly without running JS.
"""
import json
import math
import re
from typing import Any

import httpx

from social_info._time import utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

URL = "https://trendshift.io"
_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[\d+,"(.*?)"\]\)', re.DOTALL)
_FULL_NAME_RE = re.compile(r'"full_name":"([^"]+)"')
_SIMPLE_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def _safe_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        return int(parsed) if math.isfinite(parsed) else None
    return None


def _unescape_js_chunk(chunk: str) -> str:
    try:
        decoded = json.loads(f'"{chunk}"', strict=False)
        if isinstance(decoded, str) and not any(
            0xD800 <= ord(ch) <= 0xDFFF for ch in decoded
        ):
            return decoded
    except json.JSONDecodeError:
        pass
    out: list[str] = []
    i = 0
    n = len(chunk)
    while i < n:
        ch = chunk[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= n:
            out.append("\\")
            break
        nxt = chunk[i + 1]
        if nxt in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[nxt])
            i += 2
        elif nxt == "u" and i + 6 <= n and all(c in _HEX_DIGITS for c in chunk[i + 2 : i + 6]):
            code = int(chunk[i + 2 : i + 6], 16)
            if (
                0xD800 <= code <= 0xDBFF
                and i + 12 <= n
                and chunk[i + 6 : i + 8] == "\\u"
                and all(c in _HEX_DIGITS for c in chunk[i + 8 : i + 12])
            ):
                low = int(chunk[i + 8 : i + 12], 16)
                if 0xDC00 <= low <= 0xDFFF:
                    out.append(chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)))
                    i += 12
                    continue
            if 0xD800 <= code <= 0xDFFF:
                out.append(chunk[i : i + 6])
            else:
                out.append(chr(code))
            i += 6
        elif nxt == "'":
            out.append("'")
            i += 2
        else:
            out.append(chunk[i : i + 2])
            i += 2
    return "".join(out)


def _decode_streaming_payload(html: str) -> str:
    chunks = _CHUNK_RE.findall(html)
    return "".join(_unescape_js_chunk(c) for c in chunks)


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
        if not isinstance(d, dict):
            continue
        full_name = d.get("full_name")
        rank = _safe_int(d.get("rank"))
        if not isinstance(full_name, str) or not full_name.strip() or rank is None or "score" not in d:
            continue
        full_name = full_name.strip()
        key = (rank, full_name)
        if key in seen:
            continue
        seen.add(key)
        normalized = dict(d)
        normalized["rank"] = rank
        normalized["full_name"] = full_name
        out.append(normalized)
    out.sort(key=lambda x: x["rank"])
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
        rank = _safe_int(d.get("rank"))
        if not isinstance(full_name, str) or not full_name or rank is None:
            continue
        if keywords and not any(k.lower() in full_name.lower() for k in keywords):
            continue
        score = _safe_int(d.get("score"))
        desc = d.get("description")
        excerpt = str(desc)[:200] if desc is not None else ""
        url = f"https://github.com/{full_name}"
        items.append(
            Item(
                title=full_name,
                url=url,
                canonical_url=canonical_url(url),
                source="trendshift",
                source_handle=f"rising:rank-{rank}",
                source_tier=source.tier,
                posted_at=now,
                fetched_at=now,
                author=full_name.split("/")[0],
                excerpt=excerpt,
                language="en",
                metadata={"content_kind": "description"},
                engagement={
                    "rank": rank,
                    "score": score if score is not None else 0,
                },
            )
        )
    return items
