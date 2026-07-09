import re
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.reddit import fetch


@pytest.mark.asyncio
async def test_fetch_reddit_parses_old_reddit_html(httpx_mock):
    fixture = Path("tests/fixtures/reddit_top.html").read_text()
    httpx_mock.add_response(
        url=re.compile(r"https://old\.reddit\.com/r/LocalLLaMA/top.*"),
        html=fixture,
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

    # 3 things in fixture, 1 promoted → 2 real posts
    assert len(items) == 2

    by_title = {it.title: it for it in items}
    assert "Promoted ad that must be skipped" not in by_title

    self_post = by_title["Qwen3.6 Q6 is finally usable for local coding agents"]
    assert self_post.source == "reddit"
    assert self_post.source_handle == "r/LocalLLaMA"
    assert self_post.source_tier == 1
    assert self_post.url == "https://www.reddit.com/r/LocalLLaMA/comments/aaa/qwen36_q6_is_finally_usable/"
    assert self_post.author == "alice_dev"
    assert self_post.engagement == {"score": 539, "comments": 122}
    # self post → no external src in excerpt
    assert "src [" not in self_post.excerpt

    ext_post = by_title["Nvidia CUDA 13.3 landed"]
    # link points to the reddit permalink (comments), outbound captured in excerpt
    assert ext_post.url == "https://www.reddit.com/r/LocalLLaMA/comments/bbb/nvidia_cuda_133_landed/"
    assert ext_post.engagement == {"score": 498, "comments": 57}
    assert "https://example.com/cuda-13-3-release" in ext_post.excerpt
