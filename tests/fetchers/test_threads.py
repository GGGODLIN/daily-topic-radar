import json
import re
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.threads import fetch


@pytest.mark.asyncio
async def test_fetch_threads_keyword_mode(httpx_mock, monkeypatch):
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", "fake-token")
    fixture = json.loads(Path("tests/fixtures/threads_keyword_search.json").read_text())
    httpx_mock.add_response(
        url=re.compile(r"https://graph\.threads\.net/v1\.0/keyword_search.*"),
        json=fixture,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="threads_keyword",
        type="threads",
        enabled=True,
        tier=1,
        params={
            "mode": "keyword",
            "search_type": "TOP",
            "queries": ["Cursor"],
            "per_query_limit": 5,
            "time_window_hours": 24,
        },
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) == 1
    item = items[0]
    assert item.source == "threads"
    assert item.author == "taiwan_dev"
    assert item.engagement["likes"] == 88


@pytest.mark.asyncio
async def test_fetch_threads_user_mode_returns_empty(monkeypatch):
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", "fake-token")
    cfg = SourceConfig(
        id="threads_user",
        type="threads",
        enabled=True,
        tier=2,
        params={"mode": "user", "handles": []},
    )
    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)
    assert items == []
