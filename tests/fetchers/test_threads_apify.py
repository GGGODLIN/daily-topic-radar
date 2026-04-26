import json
import re
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.threads_apify import fetch


@pytest.mark.asyncio
async def test_fetch_threads_apify_parses_posts(httpx_mock, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN_THREADS", "fake-token")
    fixture = json.loads(Path("tests/fixtures/apify_threads_response.json").read_text())
    httpx_mock.add_response(
        url=re.compile(r"https://api\.apify\.com/v2/acts/.*"),
        json=fixture,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="threads_keyword",
        type="threads_apify",
        enabled=True,
        tier=1,
        params={"queries": ["Claude", "Cursor"], "per_query_limit": 2},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) == 2
    assert items[0].source == "threads"
    assert items[0].source_handle == "@wright_mode"
    assert items[0].author == "Brooke | AI Education"
    assert items[0].engagement == {"likes": 42, "comments": 7, "reposts": 3}
    assert items[0].language == "en"
    assert items[1].source_handle == "@tw_dev"
    assert items[1].language == "zh"


@pytest.mark.asyncio
async def test_fetch_threads_apify_no_token_raises(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN_THREADS", raising=False)
    cfg = SourceConfig(
        id="threads_keyword",
        type="threads_apify",
        enabled=True,
        tier=1,
        params={"queries": ["Claude"]},
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(RuntimeError, match="APIFY_TOKEN_THREADS"):
            await fetch(cfg, client)


@pytest.mark.asyncio
async def test_fetch_threads_apify_empty_queries_short_circuits(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN_THREADS", "fake-token")
    cfg = SourceConfig(
        id="threads_keyword",
        type="threads_apify",
        enabled=True,
        tier=1,
        params={"queries": []},
    )
    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)
    assert items == []
