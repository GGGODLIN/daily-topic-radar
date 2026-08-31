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


@pytest.mark.asyncio
async def test_fetch_threads_apify_relay_mode_omits_token(httpx_mock, monkeypatch):
    monkeypatch.setenv("APIFY_RELAY_URL", "http://127.0.0.1:8317")
    monkeypatch.delenv("APIFY_TOKEN_THREADS", raising=False)
    fixture = json.loads(Path("tests/fixtures/apify_threads_response.json").read_text())
    httpx_mock.add_response(
        url=re.compile(r"http://127\.0\.0\.1:8317"),
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

    req = httpx_mock.get_requests()[0]
    assert str(req.url) == (
        "http://127.0.0.1:8317/v2/acts/D15iJFBNZ9wgeWAhw/"
        "run-sync-get-dataset-items"
    )
    assert "token" not in req.url.params
    assert req.headers.get("Authorization") is None
    payload = json.loads(req.content)
    assert payload["keywords"] == ["Claude", "Cursor"]
    assert payload["maxItemsPerKeyword"] == 2
    assert len(items) == 2
    assert items[0].source == "threads"
    assert items[0].source_handle == "@wright_mode"


@pytest.mark.asyncio
async def test_fetch_threads_apify_invalid_relay_url_fail_closed(httpx_mock, monkeypatch):
    monkeypatch.setenv("APIFY_RELAY_URL", "http://127.0.0.1:8317/secret")
    monkeypatch.delenv("APIFY_TOKEN_THREADS", raising=False)
    httpx_mock.add_response(
        url=re.compile(r"http://127\.0\.0\.1:8317"),
        json=[],
        is_reusable=True,
        is_optional=True,
    )

    cfg = SourceConfig(
        id="threads_keyword",
        type="threads_apify",
        enabled=True,
        tier=1,
        params={"queries": ["Claude"]},
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(RuntimeError, match="APIFY_RELAY_URL"):
            await fetch(cfg, client)

    assert httpx_mock.get_requests() == []
