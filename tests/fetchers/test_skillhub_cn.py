import json
import re
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.skillhub_cn import fetch


def _cfg(limit: int = 8) -> SourceConfig:
    return SourceConfig(
        id="skillhub_cn",
        type="skillhub_cn",
        enabled=True,
        tier=2,
        language="zh-CN",
        params={"limit": limit},
    )


@pytest.mark.asyncio
async def test_fetch_skillhub_skills(httpx_mock):
    fixture = json.loads(Path("tests/fixtures/skillhub_cn_response.json").read_text())
    httpx_mock.add_response(
        url=re.compile(r"https://api\.skillhub\.cn/api/skills.*"),
        json=fixture,
        is_reusable=True,
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(), client)

    assert items
    assert all(it.source == "skillhub_cn" for it in items)
    assert all(it.language == "zh-CN" for it in items)
    assert all(it.url.startswith("https://skillhub.cn/") for it in items)
    assert any(it.engagement["downloads"] > 0 for it in items)


@pytest.mark.asyncio
async def test_upstream_url_is_recorded(httpx_mock):
    fixture = json.loads(Path("tests/fixtures/skillhub_cn_response.json").read_text())
    httpx_mock.add_response(
        url=re.compile(r"https://api\.skillhub\.cn/api/skills.*"),
        json=fixture,
        is_reusable=True,
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(), client)

    with_upstream = [it for it in items if it.also_appeared_in]
    assert with_upstream
    assert all(
        a["url"].startswith("http") for it in with_upstream for a in it.also_appeared_in
    )


@pytest.mark.asyncio
async def test_nonzero_api_code_raises(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"https://api\.skillhub\.cn/api/skills.*"),
        json={"code": 40001, "message": "bad request"},
        is_reusable=True,
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="code 40001"):
            await fetch(_cfg(), client)
