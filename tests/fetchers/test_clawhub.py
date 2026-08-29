import json
import re
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.clawhub import fetch


def _cfg(limit: int = 8) -> SourceConfig:
    return SourceConfig(
        id="clawhub",
        type="clawhub",
        enabled=True,
        tier=2,
        params={"limit": limit},
    )


@pytest.mark.asyncio
async def test_fetch_clawhub_skills(httpx_mock):
    fixture = json.loads(Path("tests/fixtures/clawhub_response.json").read_text())
    httpx_mock.add_response(
        url=re.compile(r"https://clawhub\.ai/api/v1/skills.*"),
        json=fixture,
        is_reusable=True,
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(), client)

    assert items
    assert all(it.source == "clawhub" for it in items)
    assert all(it.url.startswith("https://clawhub.ai/skills/") for it in items)
    assert all(it.source_tier == 2 for it in items)
    assert [it.engagement["rank"] for it in items] == list(range(1, len(items) + 1))
    assert any(it.engagement["downloads"] > 0 for it in items)


@pytest.mark.asyncio
async def test_limit_is_respected(httpx_mock):
    fixture = json.loads(Path("tests/fixtures/clawhub_response.json").read_text())
    httpx_mock.add_response(
        url=re.compile(r"https://clawhub\.ai/api/v1/skills.*"),
        json=fixture,
        is_reusable=True,
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(limit=3), client)

    assert len(items) == 3


@pytest.mark.asyncio
async def test_entry_without_slug_is_skipped(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"https://clawhub\.ai/api/v1/skills.*"),
        json={"items": [{"displayName": "no slug"}, {"slug": "ok", "stats": {"downloads": 5}}]},
        is_reusable=True,
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(limit=10), client)

    assert [it.title for it in items] == ["ok"]
