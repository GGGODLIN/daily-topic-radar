import re
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.rss import fetch


@pytest.mark.asyncio
async def test_fetch_rss_basic(httpx_mock):
    xml = Path("tests/fixtures/sample_rss.xml").read_text()
    httpx_mock.add_response(
        url=re.compile(r"https://www\.anthropic\.com/news\.rss.*"),
        text=xml,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="anthropic_blog",
        type="rss",
        enabled=True,
        tier=1,
        language="en",
        params={"url": "https://www.anthropic.com/news.rss"},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) == 2
    item = items[0]
    assert item.source == "rss"
    assert item.source_handle == "anthropic_blog"
    assert item.title == "Claude 4.7 Opus released"
    assert item.excerpt


@pytest.mark.asyncio
async def test_fetch_rss_sends_configured_user_agent(httpx_mock):
    xml = Path("tests/fixtures/sample_rss.xml").read_text()
    custom_ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/131.0.0.0"
    httpx_mock.add_response(
        url="https://venturebeat.com/feed/",
        text=xml,
    )

    cfg = SourceConfig(
        id="venturebeat_ai",
        type="rss",
        enabled=True,
        tier=2,
        language="en",
        params={"url": "https://venturebeat.com/feed/", "user_agent": custom_ua},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) == 2
    req = httpx_mock.get_request()
    assert req is not None
    assert req.headers["user-agent"] == custom_ua

