import re
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.rsshub import fetch


@pytest.mark.asyncio
async def test_fetch_rsshub_path_appended_to_instance(httpx_mock, monkeypatch):
    monkeypatch.setenv("RSSHUB_INSTANCE_URL", "https://rsshub.app")
    xml = Path("tests/fixtures/sample_rss.xml").read_text()
    httpx_mock.add_response(
        url=re.compile(r"https://rsshub\.app/zhihu/hot.*"),
        text=xml,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="zhihu_hot",
        type="rsshub",
        enabled=True,
        tier=1,
        language="zh-CN",
        params={"path": "/zhihu/hot"},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) == 2
    assert items[0].source == "rsshub"
    assert items[0].source_handle == "zhihu_hot"
    assert items[0].language == "zh-CN"


@pytest.mark.asyncio
async def test_fetch_rsshub_unescapes_html_entities_in_title_and_excerpt(httpx_mock, monkeypatch):
    monkeypatch.setenv("RSSHUB_INSTANCE_URL", "https://rsshub.app")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><title>36kr</title>'
        '<item>'
        '<title>AI &amp;amp; Agent 時代</title>'
        '<link>https://36kr.com/p/1</link>'
        '<description>&lt;p&gt;文&amp;nbsp;|&amp;nbsp;王智鹏 &amp;quot;實測&amp;quot;&lt;/p&gt;</description>'
        '</item></channel></rss>'
    )
    httpx_mock.add_response(url="https://rsshub.app/36kr/motif/1", text=xml)

    cfg = SourceConfig(
        id="36kr_feed",
        type="rsshub",
        enabled=True,
        tier=2,
        language="zh-CN",
        params={"path": "/36kr/motif/1"},
    )
    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) == 1
    assert "&nbsp;" not in items[0].excerpt
    assert "&quot;" not in items[0].excerpt
    assert items[0].excerpt == '文 | 王智鹏 "實測"'
    assert items[0].title == "AI & Agent 時代"

