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


@pytest.mark.asyncio
async def test_fetch_rss_unescapes_double_encoded_wordpress_entities(httpx_mock):
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        '<channel><title>BlockTempo</title>'
        '<item>'
        '<title>Gemini &amp;#8212; 新進展</title>'
        '<link>https://www.blocktempo.com/a</link>'
        '<description><![CDATA[<p>摘要文字 [&amp;#8230;]</p>]]></description>'
        '<content:encoded><![CDATA[<p>全文 [&amp;#8217;] 內容</p>]]></content:encoded>'
        '</item></channel></rss>'
    )
    httpx_mock.add_response(url="https://www.blocktempo.com/feed/", text=xml)
    cfg = SourceConfig(
        id="blocktempo_ai",
        type="rss",
        enabled=True,
        tier=2,
        language="zh-TW",
        params={"url": "https://www.blocktempo.com/feed/"},
    )
    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) == 1
    assert "&#8230;" not in items[0].excerpt
    assert "[…]" in items[0].excerpt
    assert "&#8217;" not in items[0].content
    assert "[’]" in items[0].content
    assert items[0].title == "Gemini — 新進展"


