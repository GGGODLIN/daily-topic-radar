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
