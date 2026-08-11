import re
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers import reddit
from social_info.fetchers.reddit import fetch


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    """Collapse the inter-request gap and backoff so tests do not sleep."""
    monkeypatch.setattr(reddit, "MIN_REQUEST_GAP_SECONDS", 0.0)
    monkeypatch.setattr(reddit, "THROTTLED_RETRY_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(reddit, "_last_request_finished_at", None)
    monkeypatch.setattr(reddit, "_first_request_started_at", None)


def _cfg(limit: int = 10) -> SourceConfig:
    return SourceConfig(
        id="reddit_localllama",
        type="reddit",
        enabled=True,
        tier=1,
        params={"subreddit": "LocalLLaMA", "time_window": "day", "limit": limit},
    )


def _add_feed(httpx_mock, **kwargs):
    httpx_mock.add_response(
        url=re.compile(r"https://www\.reddit\.com/r/LocalLLaMA/top\.rss.*"),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_fetch_reddit_parses_top_rss(httpx_mock):
    fixture = Path("tests/fixtures/reddit_top.rss").read_text()
    _add_feed(httpx_mock, text=fixture, is_reusable=True)

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(), client)

    # 4 entries in fixture, 1 with an empty title → 3 usable posts
    assert len(items) == 3

    by_title = {it.title: it for it in items}
    self_post = by_title["Qwen3.6 Q6 is finally usable for local coding agents"]
    assert self_post.source == "reddit"
    assert self_post.source_handle == "r/LocalLLaMA"
    assert self_post.source_tier == 1
    assert self_post.url == "https://www.reddit.com/r/LocalLLaMA/comments/aaa/qwen36_q6_is_finally_usable/"
    assert self_post.author == "alice_dev"
    assert self_post.language == "en"
    assert self_post.posted_at == datetime(2026, 8, 10, 13, 0, 29)
    # selftext is only available on the rss path — the old listing HTML had none
    assert self_post.excerpt == "After a week of testing, Qwen3.6 Q6 finally holds up as a local coding agent."


@pytest.mark.asyncio
async def test_selftext_excerpt_drops_the_submitted_by_boilerplate(httpx_mock):
    """Reddit appends 'submitted by /u/x [link] [comments]' to every entry body."""
    fixture = Path("tests/fixtures/reddit_top.rss").read_text()
    _add_feed(httpx_mock, text=fixture, is_reusable=True)

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(), client)

    for it in items:
        assert "submitted by" not in it.excerpt
        assert "[comments]" not in it.excerpt
        assert "&#32;" not in it.excerpt


@pytest.mark.asyncio
async def test_link_post_excerpt_carries_the_outbound_url(httpx_mock):
    fixture = Path("tests/fixtures/reddit_top.rss").read_text()
    _add_feed(httpx_mock, text=fixture, is_reusable=True)

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(), client)

    by_title = {it.title: it for it in items}
    ext_post = by_title["Nvidia CUDA 13.3 landed"]
    assert ext_post.url == "https://www.reddit.com/r/LocalLLaMA/comments/bbb/nvidia_cuda_133_landed/"
    assert ext_post.excerpt == "src [https://example.com/cuda-13-3-release](https://example.com/cuda-13-3-release)"

    # a gallery post's [link] points back into reddit → not an outbound link
    gallery_post = by_title["Gallery post with benchmarks"]
    assert "reddit.com/gallery" not in gallery_post.excerpt
    assert gallery_post.excerpt == "Benchmarks in the gallery."


@pytest.mark.asyncio
async def test_rss_carries_no_engagement_numbers(httpx_mock):
    """score / num_comments are absent from rss; feed order is the only signal."""
    fixture = Path("tests/fixtures/reddit_top.rss").read_text()
    _add_feed(httpx_mock, text=fixture, is_reusable=True)

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(), client)

    assert all(it.engagement == {} for it in items)
    assert [it.title for it in items] == [
        "Qwen3.6 Q6 is finally usable for local coding agents",
        "Nvidia CUDA 13.3 landed",
        "Gallery post with benchmarks",
    ]


@pytest.mark.asyncio
async def test_limit_caps_returned_items(httpx_mock):
    fixture = Path("tests/fixtures/reddit_top.rss").read_text()
    _add_feed(httpx_mock, text=fixture, is_reusable=True)

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(limit=1), client)

    assert len(items) == 1


@pytest.mark.asyncio
async def test_retries_once_after_429_then_succeeds(httpx_mock):
    fixture = Path("tests/fixtures/reddit_top.rss").read_text()
    _add_feed(httpx_mock, status_code=429, text="")
    _add_feed(httpx_mock, text=fixture)

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(), client)

    assert len(items) == 3
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.asyncio
async def test_persistent_429_raises_so_pipeline_records_a_failure(httpx_mock):
    """A throttled-out source must raise, not return [] — otherwise it would be
    indistinguishable from the silent-failure mode this fetcher was rewritten to
    escape."""
    _add_feed(httpx_mock, status_code=429, text="", is_reusable=True)

    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError) as exc:
            await fetch(_cfg(), client)

    assert exc.value.response.status_code == 429


@pytest.mark.asyncio
async def test_redirect_to_login_page_yields_no_items(httpx_mock):
    """Regression for 2026-08-11: a 200 login page must not parse as posts."""
    _add_feed(httpx_mock, text="<html><body>login required</body></html>", is_reusable=True)

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(), client)

    assert items == []
