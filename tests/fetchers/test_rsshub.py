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
