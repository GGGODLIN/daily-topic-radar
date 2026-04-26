import json
import re
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.twitter import fetch


@pytest.mark.asyncio
async def test_fetch_twitter_via_apify(httpx_mock, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN_TWITTER", "fake-token")
    fixture = json.loads(Path("tests/fixtures/apify_tweet_scraper_response.json").read_text())
    httpx_mock.add_response(
        url=re.compile(r"https://api\.apify\.com/v2/acts/.*"),
        json=fixture,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="twitter_tier1",
        type="twitter",
        enabled=True,
        tier=1,
        params={"handles": ["sama"], "per_handle_limit": 10, "time_window_hours": 24},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) == 1
    item = items[0]
    assert item.source == "x"
    assert item.source_handle == "@sama"
    assert item.engagement["likes"] == 734
    assert item.engagement["comments"] == 38
    assert item.engagement["retweets"] == 9


@pytest.mark.asyncio
async def test_fetch_twitter_skips_mock_tweet(httpx_mock, monkeypatch):
    """Apify actor returns mock_tweet entries when the underlying search has no
    real results; the fetcher must filter them out."""
    monkeypatch.setenv("APIFY_TOKEN_TWITTER", "fake-token")
    httpx_mock.add_response(
        url=re.compile(r"https://api\.apify\.com/v2/acts/.*"),
        json=[
            {"type": "mock_tweet", "id": -1, "text": "minimum charge..."},
            {"type": "mock_tweet", "id": -2, "text": "more mock..."},
        ],
        is_reusable=True,
    )
    cfg = SourceConfig(
        id="twitter_tier1",
        type="twitter",
        enabled=True,
        tier=1,
        params={"handles": ["nobody"]},
    )
    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)
    assert items == []


@pytest.mark.asyncio
async def test_fetch_twitter_no_token_raises(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN_TWITTER", raising=False)
    cfg = SourceConfig(
        id="twitter_tier1",
        type="twitter",
        enabled=True,
        tier=1,
        params={"handles": ["karpathy"]},
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(RuntimeError, match="APIFY_TOKEN_TWITTER"):
            await fetch(cfg, client)
