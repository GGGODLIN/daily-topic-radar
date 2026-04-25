import json
import re
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.twitter import fetch


@pytest.mark.asyncio
async def test_fetch_twitter_per_handle(httpx_mock, monkeypatch):
    monkeypatch.setenv("TWITTERAPI_IO_KEY", "fake-key")
    fixture = json.loads(Path("tests/fixtures/twitterapi_user_tweets.json").read_text())
    httpx_mock.add_response(
        url=re.compile(r"https://api\.twitterapi\.io.*"),
        json=fixture,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="twitter_tier1",
        type="twitter",
        enabled=True,
        tier=1,
        params={"handles": ["karpathy"], "per_handle_limit": 10, "time_window_hours": 24},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) == 1
    item = items[0]
    assert item.source == "x"
    assert item.source_handle == "@karpathy"
    assert item.engagement["likes"] == 1234


@pytest.mark.asyncio
async def test_fetch_twitter_no_key_raises(monkeypatch):
    monkeypatch.delenv("TWITTERAPI_IO_KEY", raising=False)
    cfg = SourceConfig(
        id="twitter_tier1",
        type="twitter",
        enabled=True,
        tier=1,
        params={"handles": ["karpathy"]},
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(RuntimeError, match="TWITTERAPI_IO_KEY"):
            await fetch(cfg, client)
