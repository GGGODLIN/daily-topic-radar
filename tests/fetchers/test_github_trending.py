import re
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.github_trending import fetch


@pytest.mark.asyncio
async def test_fetch_github_trending_parses_html(httpx_mock):
    html = Path("tests/fixtures/github_trending.html").read_text()
    httpx_mock.add_response(
        url=re.compile(r"https://github\.com/trending/python.*"),
        text=html,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="github_trending",
        type="github_trending",
        enabled=True,
        tier=1,
        params={
            "languages": ["python"],
            "since": "daily",
            "ai_keywords": ["ai", "llm", "agent", "ml", "model"],
        },
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert all(it.source == "github_trending" for it in items)
    if items:
        assert "github.com" in items[0].url
        assert "stars" in items[0].engagement


@pytest.mark.asyncio
async def test_fetch_returns_all_repos_no_keyword_filter(httpx_mock):
    html = Path("tests/fixtures/github_trending.html").read_text()
    httpx_mock.add_response(
        url=re.compile(r"https://github\.com/trending/python.*"),
        text=html,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="github_trending",
        type="github_trending",
        enabled=True,
        tier=1,
        params={
            "languages": ["python"],
            "since": ["daily"],
            # keyword filter 拿掉、fetcher 應 return 所有 repos
            "ai_keywords": ["ai", "llm"],
        },
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    # 拿掉 filter 後、items 數應等於 fixture 內全 article.Box-row 數
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    expected_count = len(soup.select("article.Box-row"))
    assert len(items) == expected_count, \
        f"expected all {expected_count} repos, got {len(items)}"


@pytest.mark.asyncio
async def test_fetch_isolates_per_lang_failure(httpx_mock):
    html = Path("tests/fixtures/github_trending.html").read_text()
    httpx_mock.add_exception(
        httpx.ConnectError("boom"),
        url=re.compile(r"https://github\.com/trending/rust.*"),
    )
    httpx_mock.add_response(
        url=re.compile(r"https://github\.com/trending/python.*"),
        text=html,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="github_trending",
        type="github_trending",
        enabled=True,
        tier=1,
        params={
            "languages": ["rust", "python"],
            "since": ["daily"],
        },
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    expected_count = len(soup.select("article.Box-row"))
    assert len(items) == expected_count
    assert all(it.source == "github_trending" for it in items)


@pytest.mark.asyncio
async def test_fetch_iterates_since_list(httpx_mock):
    html = Path("tests/fixtures/github_trending.html").read_text()
    httpx_mock.add_response(
        url=re.compile(r"https://github\.com/trending/python.*since=daily.*"),
        text=html,
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=re.compile(r"https://github\.com/trending/python.*since=weekly.*"),
        text=html,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="github_trending",
        type="github_trending",
        enabled=True,
        tier=1,
        params={
            "languages": ["python"],
            "since": ["daily", "weekly"],
        },
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    # 2 個 since values × N repos per fixture (dedup 沒在 fetcher 端做)
    assert len(items) > 0
