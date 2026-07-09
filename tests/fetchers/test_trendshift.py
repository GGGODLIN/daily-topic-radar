import re
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.trendshift import fetch


@pytest.mark.asyncio
async def test_fetch_trendshift_parses_current_structure(httpx_mock):
    html = Path("tests/fixtures/trendshift.html").read_text()
    httpx_mock.add_response(
        url=re.compile(r"https://trendshift\.io.*"),
        text=html,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="trendshift",
        type="trendshift",
        enabled=True,
        tier=1,
        params={"limit": 25},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) > 0, "trendshift fetch returned empty — parser out of sync"
    assert all(it.source == "trendshift" for it in items)
    assert all("github.com" in it.url for it in items)
    assert all(it.source_handle.startswith("rising:rank-") for it in items)
    assert all("score" in it.engagement for it in items)


@pytest.mark.asyncio
async def test_fetch_trendshift_respects_limit(httpx_mock):
    html = Path("tests/fixtures/trendshift.html").read_text()
    httpx_mock.add_response(
        url=re.compile(r"https://trendshift\.io.*"),
        text=html,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="trendshift",
        type="trendshift",
        enabled=True,
        tier=1,
        params={"limit": 5},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) == 5
