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
async def test_fetch_twitter_all_mock_raises(httpx_mock, monkeypatch):
    """Apify actor returns mock_tweet placeholders when the underlying X search
    has no real results (KaitoEasyAPI bills a minimum charge per call and pads
    the dataset to meet it). An all-mock response means the source silently
    produced nothing — raise so it lands in KNOWN_ISSUES instead of vanishing
    as a clean `ok / items_fetched=0`.

    Real incident 2026-07-31: twitter_tier1 and twitter_anthropic both returned
    15 mock_tweet records; both were recorded ok/0 and the digest lost the whole
    X layer with no alert.
    """
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
        with pytest.raises(RuntimeError, match="0 usable tweets"):
            await fetch(cfg, client)


@pytest.mark.asyncio
async def test_fetch_twitter_mixed_mock_and_real_keeps_real(httpx_mock, monkeypatch):
    """Partial padding is normal — the actor tops a short result set up to its
    minimum. As long as at least one real tweet survives, filter the mocks and
    return quietly (no alert)."""
    monkeypatch.setenv("APIFY_TOKEN_TWITTER", "fake-token")
    real = json.loads(Path("tests/fixtures/apify_tweet_scraper_response.json").read_text())
    httpx_mock.add_response(
        url=re.compile(r"https://api\.apify\.com/v2/acts/.*"),
        json=[
            {"type": "mock_tweet", "id": -1, "text": "minimum charge..."},
            *real,
            {"type": "mock_tweet", "id": -2, "text": "more mock..."},
        ],
        is_reusable=True,
    )
    cfg = SourceConfig(
        id="twitter_anthropic",
        type="twitter",
        enabled=True,
        tier=1,
        params={"handles": ["sama"]},
    )
    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)
    assert len(items) == 1
    assert items[0].source_handle == "@sama"


@pytest.mark.asyncio
async def test_fetch_twitter_empty_dataset_returns_empty(httpx_mock, monkeypatch):
    """A genuinely empty dataset is not the padding failure mode — keep the
    existing quiet-empty behaviour so this stays distinguishable from all-mock."""
    monkeypatch.setenv("APIFY_TOKEN_TWITTER", "fake-token")
    httpx_mock.add_response(
        url=re.compile(r"https://api\.apify\.com/v2/acts/.*"),
        json=[],
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
