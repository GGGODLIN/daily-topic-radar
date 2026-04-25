import json
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.hn import fetch


@pytest.mark.asyncio
async def test_fetch_hn_parses_response(httpx_mock):
    fixture = json.loads(Path("tests/fixtures/hn_response.json").read_text())
    import re
    httpx_mock.add_response(
        url=re.compile(r"https://hn\.algolia\.com/api/v1/search_by_date.*"),
        json=fixture,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="hn",
        type="hn_algolia",
        enabled=True,
        tier=1,
        params={"keywords": [], "limit": 30},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) > 0
    item = items[0]
    assert item.source == "hn"
    assert item.source_handle == "front_page"
    assert item.source_tier == 1
    assert item.url
    assert item.title
    assert "score" in item.engagement


@pytest.mark.asyncio
async def test_fetch_hn_filters_by_keyword(httpx_mock):
    fixture = {
        "hits": [
            {
                "objectID": "1",
                "title": "AI breakthrough in agents",
                "url": "https://example.com/a",
                "author": "u1",
                "points": 100,
                "num_comments": 10,
                "created_at": "2026-04-26T08:00:00.000Z",
            },
            {
                "objectID": "2",
                "title": "Cooking pasta the Italian way",
                "url": "https://example.com/b",
                "author": "u2",
                "points": 50,
                "num_comments": 5,
                "created_at": "2026-04-26T08:30:00.000Z",
            },
        ]
    }
    import re
    httpx_mock.add_response(
        url=re.compile(r"https://hn\.algolia\.com/api/v1/search_by_date.*"),
        json=fixture,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="hn",
        type="hn_algolia",
        enabled=True,
        tier=1,
        params={"keywords": ["AI"], "limit": 30},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) == 1
    assert "AI" in items[0].title
