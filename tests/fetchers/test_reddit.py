import json
import re
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.reddit import fetch


@pytest.mark.asyncio
async def test_fetch_reddit_parses_top_json(httpx_mock):
    fixture = json.loads(Path("tests/fixtures/reddit_response.json").read_text())
    httpx_mock.add_response(
        url=re.compile(r"https://www\.reddit\.com/r/LocalLLaMA/top\.json.*"),
        json=fixture,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="reddit_localllama",
        type="reddit",
        enabled=True,
        tier=1,
        params={"subreddit": "LocalLLaMA", "time_window": "day", "limit": 10},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) > 0
    item = items[0]
    assert item.source == "reddit"
    assert item.source_handle == "r/LocalLLaMA"
    assert item.source_tier == 1
    assert item.url
    assert "score" in item.engagement
    assert "comments" in item.engagement
