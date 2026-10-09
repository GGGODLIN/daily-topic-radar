import json
import re
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.twitter import fetch

FIXTURE = Path("tests/fixtures/scrapebadger_tweet_response.json")
ACTOR_PATH = (
    "/v2/acts/scrape.badger~twitter-tweets-scraper/run-sync-get-dataset-items"
)


def _cfg(handles: list[str], **params) -> SourceConfig:
    merged = {"handles": handles, "time_window_hours": 24}
    merged.update(params)
    return SourceConfig(
        id="twitter_tier1",
        type="twitter",
        enabled=True,
        tier=1,
        params=merged,
    )


@pytest.mark.asyncio
async def test_fetch_twitter_via_apify(httpx_mock, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN_TWITTER", "fake-token")
    fixture = json.loads(FIXTURE.read_text())
    httpx_mock.add_response(
        url=re.compile(r"https://api\.apify\.com/v2/acts/.*"),
        json=fixture,
    )
    httpx_mock.add_response(
        url=re.compile(r"https://api\.apify\.com/v2/acts/.*"),
        json=[{
            "id": "555",
            "username": "karpathy",
            "user_name": "Andrej Karpathy",
            "text": "notes on agents",
            "created_at": "2026-10-09T09:00:03Z",
            "favorite_count": 3,
            "retweet_count": 1,
            "reply_count": 0,
        }],
    )

    cfg = _cfg(["sama", "karpathy"], per_handle_limit=10)

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    requests = httpx_mock.get_requests()
    assert len(requests) == 2
    first, second = (json.loads(req.content) for req in requests)
    assert first["mode"] == "Advanced Search"
    assert first["query_type"] == "Latest"
    assert first["max_results"] == 10
    assert first["query"].startswith("from:sama since:")
    assert " until:" in first["query"]
    assert "from:karpathy" not in first["query"]
    assert " OR " not in first["query"]
    assert second["query"].startswith("from:karpathy since:")
    assert second["max_results"] == 10
    assert "maxItems" not in first
    assert "searchTerms" not in first
    assert "maxTotalChargeUsd" not in first
    assert requests[0].url.params["maxTotalChargeUsd"] == "0.01"
    assert ACTOR_PATH in str(requests[0].url)

    assert len(items) == 2
    item = items[0]
    assert item.source == "x"
    assert item.source_tier == 1
    assert item.source_handle == "@sama"
    assert item.author == "Sam Altman"
    assert item.url == "https://x.com/sama/status/2048167247278207182"
    assert item.posted_at == datetime(2026, 4, 25, 22, 28, 19)
    assert item.engagement["likes"] == 734
    assert item.engagement["comments"] == 38
    assert item.engagement["retweets"] == 9
    assert items[1].source_handle == "@karpathy"
    assert items[1].posted_at == datetime(2026, 10, 9, 9, 0, 3)


@pytest.mark.asyncio
async def test_fetch_twitter_all_mock_raises(httpx_mock, monkeypatch):
    """非空但全是 mock 要進既有失敗路徑。不再為了第二種 query shape 多打一發。"""
    monkeypatch.setenv("APIFY_TOKEN_TWITTER", "fake-token")
    httpx_mock.add_response(
        url=re.compile(r"https://api\.apify\.com/v2/acts/.*"),
        json=[
            {"type": "mock_tweet", "id": -1, "text": "minimum charge..."},
            {"type": "mock_tweet", "id": -2, "text": "more mock..."},
        ],
        is_reusable=True,
    )
    cfg = _cfg(["nobody", "else"])
    async with httpx.AsyncClient() as client:
        with pytest.raises(RuntimeError, match="0 usable tweets") as exc:
            await fetch(cfg, client)
    message = str(exc.value)
    assert "searchTerms" not in message
    assert "twitterContent" not in message
    requests = httpx_mock.get_requests()
    assert len(requests) == 2
    for req, handle in zip(requests, ["nobody", "else"], strict=True):
        payload = json.loads(req.content)
        assert payload["query"].startswith(f"from:{handle} since:")
        assert "searchTerms" not in payload
        assert "twitterContent" not in payload


@pytest.mark.asyncio
async def test_fetch_twitter_matt_limit_stays_50(httpx_mock, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN_TWITTER", "fake-token")
    httpx_mock.add_response(
        url=re.compile(r"https://api\.apify\.com/v2/acts/.*"),
        json=json.loads(FIXTURE.read_text()),
    )
    cfg = _cfg(["mattpocockuk"], per_handle_limit=50)
    async with httpx.AsyncClient() as client:
        await fetch(cfg, client)
    payload = json.loads(httpx_mock.get_requests()[0].content)
    assert payload["max_results"] == 50
    assert payload["query"].startswith("from:mattpocockuk since:")


@pytest.mark.asyncio
async def test_fetch_twitter_rfc_and_iso_become_utc_naive(httpx_mock, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN_TWITTER", "fake-token")
    httpx_mock.add_response(
        url=re.compile(r"https://api\.apify\.com/v2/acts/.*"),
        json=[
            {
                "id": "1",
                "username": "example",
                "user_name": "Example",
                "text": "rfc",
                "created_at": "Fri Oct 09 09:00:03 +0000 2026",
                "favorite_count": 12,
                "retweet_count": 3,
                "reply_count": 2,
            },
            {
                "id": "2",
                "username": "example",
                "user_name": "Example",
                "text": "iso offset",
                "created_at": "2026-10-09T17:00:03+08:00",
                "favorite_count": 1,
                "retweet_count": 0,
                "reply_count": 4,
            },
        ],
    )
    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(["example"]), client)
    assert items[0].posted_at == datetime(2026, 10, 9, 9, 0, 3)
    assert items[0].posted_at.tzinfo is None
    assert items[1].posted_at == datetime(2026, 10, 9, 9, 0, 3)
    assert items[1].posted_at.tzinfo is None
    assert items[0].url == "https://x.com/example/status/1"
    assert items[0].engagement == {"likes": 12, "comments": 2, "retweets": 3}
    assert items[1].engagement["comments"] == 4


@pytest.mark.asyncio
async def test_fetch_twitter_mixed_mock_and_real_keeps_real(httpx_mock, monkeypatch):
    """mock 與缺欄位列丟掉；沒有 type=tweet 的正常列留下。"""
    monkeypatch.setenv("APIFY_TOKEN_TWITTER", "fake-token")
    real = json.loads(FIXTURE.read_text())
    httpx_mock.add_response(
        url=re.compile(r"https://api\.apify\.com/v2/acts/.*"),
        json=[
            {"type": "mock_tweet", "id": -1, "text": "minimum charge..."},
            {"id": "9", "text": "no username", "username": ""},
            *real,
            {"type": "mock_tweet", "id": -2, "text": "more mock..."},
        ],
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
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.asyncio
async def test_fetch_twitter_empty_dataset_returns_empty(httpx_mock, monkeypatch):
    """空 dataset 不是失敗。前一個 handle 是空的，仍要打下一個。"""
    monkeypatch.setenv("APIFY_TOKEN_TWITTER", "fake-token")
    httpx_mock.add_response(
        url=re.compile(r"https://api\.apify\.com/v2/acts/.*"),
        json=[],
    )
    httpx_mock.add_response(
        url=re.compile(r"https://api\.apify\.com/v2/acts/.*"),
        json=[],
    )
    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(["nobody", "quiet"]), client)
    assert items == []
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.asyncio
async def test_fetch_twitter_no_token_raises(monkeypatch):
    monkeypatch.setenv("APIFY_RELAY_URL", "")
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


@pytest.mark.asyncio
async def test_fetch_twitter_http_error_is_not_retried(httpx_mock, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN_TWITTER", "fake-token")
    httpx_mock.add_response(
        url=re.compile(r"https://api\.apify\.com/v2/acts/.*"),
        status_code=502,
        json={"error": "bad gateway"},
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError) as exc:
            await fetch(_cfg(["sama", "karpathy"], per_handle_limit=10), client)
    assert exc.value.response.status_code == 502
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.asyncio
async def test_fetch_twitter_duplicate_url_emitted_once(httpx_mock, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN_TWITTER", "fake-token")
    row = json.loads(FIXTURE.read_text())
    httpx_mock.add_response(
        url=re.compile(r"https://api\.apify\.com/v2/acts/.*"),
        json=row,
        is_reusable=True,
    )
    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(["sama", "karpathy"]), client)
    assert len(httpx_mock.get_requests()) == 2
    assert len(items) == 1
    assert items[0].url == "https://x.com/sama/status/2048167247278207182"


@pytest.mark.asyncio
async def test_fetch_twitter_relay_mode_omits_token(httpx_mock, monkeypatch):
    monkeypatch.setenv("APIFY_RELAY_URL", "http://127.0.0.1:8317")
    monkeypatch.delenv("APIFY_TOKEN_TWITTER", raising=False)
    fixture = json.loads(FIXTURE.read_text())
    httpx_mock.add_response(
        url=re.compile(r"http://127\.0\.0\.1:8317"),
        json=fixture,
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(["sama"], per_handle_limit=10), client)

    req = httpx_mock.get_requests()[0]
    assert req.url.path == ACTOR_PATH
    assert str(req.url).startswith("http://127.0.0.1:8317/v2/acts/")
    assert "token" not in req.url.params
    assert req.url.params["maxTotalChargeUsd"] == "0.01"
    assert req.headers.get("Authorization") is None
    payload = json.loads(req.content)
    assert payload["query"].startswith("from:sama since:")
    assert payload["max_results"] == 10
    assert len(items) == 1
    assert items[0].source == "x"
    assert items[0].engagement["likes"] == 734
