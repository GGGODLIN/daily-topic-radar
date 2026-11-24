import re
from datetime import datetime

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


def _cfg(limit: int = 10, **extra) -> SourceConfig:
    params = {"subreddit": "LocalLLaMA", "time_window": "day", "limit": limit, **extra}
    return SourceConfig(
        id="reddit_localllama",
        type="reddit",
        enabled=True,
        tier=1,
        params=params,
    )


def _add_search(httpx_mock, **kwargs):
    httpx_mock.add_response(
        url=re.compile(r"https://arctic-shift\.photon-reddit\.com/api/posts/search.*"),
        **kwargs,
    )


def _sample_posts() -> list[dict]:
    return [
        {
            "id": "aaa",
            "title": "Qwen3.6 Q6 is finally usable for local coding agents",
            "permalink": "/r/LocalLLaMA/comments/aaa/qwen36_q6_is_finally_usable/",
            "author": "alice_dev",
            "created_utc": 1786366829,  # 2026-08-10 13:00:29 UTC
            "score": 420,
            "num_comments": 88,
            "is_self": True,
            "selftext": "After a week of testing,\n\nQwen3.6 Q6 finally holds up as a local coding agent.",
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/aaa/qwen36_q6_is_finally_usable/",
            "removed_by_category": None,
        },
        {
            "id": "bbb",
            "title": "Nvidia CUDA 13.3 landed",
            "permalink": "/r/LocalLLaMA/comments/bbb/nvidia_cuda_133_landed/",
            "author": "/u/bob_gpu",
            "created_utc": 1786366000,
            "score": 910,
            "num_comments": 150,
            "is_self": False,
            "selftext": "",
            "url": "https://example.com/cuda-13-3-release",
            "removed_by_category": None,
        },
        {
            "id": "ccc",
            "title": "Gallery post with benchmarks",
            "permalink": "/r/LocalLLaMA/comments/ccc/gallery_post/",
            "author": "carol",
            "created_utc": 1786365000,
            "score": 120,
            "num_comments": 12,
            "is_self": False,
            "selftext": "Benchmarks in the gallery.",
            "url": "https://www.reddit.com/gallery/ccc",
            "removed_by_category": None,
        },
        {
            "id": "ddd",
            "title": "Removed by mod",
            "permalink": "/r/LocalLLaMA/comments/ddd/removed/",
            "author": "spammer",
            "created_utc": 1786364000,
            "score": 9999,
            "num_comments": 500,
            "is_self": True,
            "selftext": "spam",
            "removed_by_category": "moderator",
        },
        {
            "id": "eee",
            "title": "Body removed",
            "permalink": "/r/LocalLLaMA/comments/eee/body_removed/",
            "author": "user2",
            "created_utc": 1786363000,
            "score": 5000,
            "num_comments": 200,
            "is_self": True,
            "selftext": "[removed]",
            "removed_by_category": None,
        },
        {
            "id": "fff",
            "title": "   ",
            "permalink": "/r/LocalLLaMA/comments/fff/blank/",
            "author": "user3",
            "created_utc": 1786362000,
            "score": 100,
            "num_comments": 5,
        },
    ]


@pytest.mark.asyncio
async def test_fetch_reddit_parses_arctic_shift_ranks_and_filters_removed(httpx_mock):
    _add_search(httpx_mock, json={"data": _sample_posts()}, is_reusable=True)

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(), client)

    # 6 raw posts: 3 filtered (removed_by_category, [removed], blank title) -> 3 usable
    assert len(items) == 3
    # Ranked by score descending
    assert [it.title for it in items] == [
        "Nvidia CUDA 13.3 landed",
        "Qwen3.6 Q6 is finally usable for local coding agents",
        "Gallery post with benchmarks",
    ]

    ext_post, self_post, gallery_post = items
    assert ext_post.engagement == {"score": 910, "comments": 150}
    assert ext_post.author == "bob_gpu"
    assert ext_post.excerpt == "src [https://example.com/cuda-13-3-release](https://example.com/cuda-13-3-release)"

    assert self_post.source == "reddit"
    assert self_post.source_handle == "r/LocalLLaMA"
    assert self_post.source_tier == 1
    assert self_post.url == "https://www.reddit.com/r/LocalLLaMA/comments/aaa/qwen36_q6_is_finally_usable/"
    assert self_post.author == "alice_dev"
    assert self_post.language == "en"
    assert self_post.posted_at == datetime(2026, 8, 10, 13, 0, 29)
    assert self_post.excerpt == "After a week of testing, Qwen3.6 Q6 finally holds up as a local coding agent."
    assert self_post.engagement == {"score": 420, "comments": 88}
    assert self_post.metadata["id"] == "aaa"

    # Gallery URL points inside reddit -> falls back to selftext
    assert "reddit.com/gallery" not in gallery_post.excerpt
    assert gallery_post.excerpt == "Benchmarks in the gallery."


@pytest.mark.asyncio
async def test_limit_caps_returned_items(httpx_mock):
    _add_search(httpx_mock, json={"data": _sample_posts()}, is_reusable=True)

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(limit=1), client)

    assert len(items) == 1
    assert items[0].title == "Nvidia CUDA 13.3 landed"


@pytest.mark.asyncio
async def test_paginates_backwards_with_before_and_dedups(httpx_mock, monkeypatch):
    monkeypatch.setattr(reddit, "PAGE_LIMIT", 2)
    now_ts = int(datetime(2026, 11, 24, 4, 0, 0).timestamp())
    monkeypatch.setattr(reddit, "utcnow", lambda: datetime(2026, 11, 24, 4, 0, 0))

    page1 = [
        {"id": "p1", "title": "Recent low score", "permalink": "/r/LocalLLaMA/comments/p1/a/", "created_utc": now_ts - 600, "score": 5, "num_comments": 1},
        {"id": "p2", "title": "Older mid score", "permalink": "/r/LocalLLaMA/comments/p2/b/", "created_utc": now_ts - 3600, "score": 20, "num_comments": 4},
    ]
    page2 = [
        {"id": "p2", "title": "Older mid score", "permalink": "/r/LocalLLaMA/comments/p2/b/", "created_utc": now_ts - 3600, "score": 20, "num_comments": 4},
        {"id": "p3", "title": "Earlier high score", "permalink": "/r/LocalLLaMA/comments/p3/c/", "created_utc": now_ts - 18000, "score": 800, "num_comments": 90},
    ]
    page3 = [
        {"id": "p4", "title": "Oldest post", "permalink": "/r/LocalLLaMA/comments/p4/d/", "created_utc": now_ts - 36000, "score": 50, "num_comments": 3},
    ]
    _add_search(httpx_mock, json={"data": page1})
    _add_search(httpx_mock, json={"data": page2})
    _add_search(httpx_mock, json={"data": page3})

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(limit=2, max_pages=5), client)

    reqs = httpx_mock.get_requests()
    assert len(reqs) == 3
    assert "before" not in reqs[0].url.params
    assert reqs[1].url.params["before"] == str(now_ts - 3601)
    assert reqs[2].url.params["before"] == str(now_ts - 18001)
    assert [it.title for it in items] == ["Earlier high score", "Oldest post"]


@pytest.mark.asyncio
async def test_retries_after_429_then_succeeds(httpx_mock):
    _add_search(httpx_mock, status_code=429, text="")
    _add_search(httpx_mock, json={"data": _sample_posts()})

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(), client)

    assert len(items) == 3
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.asyncio
async def test_persistent_429_raises_so_pipeline_records_a_failure(httpx_mock):
    _add_search(httpx_mock, status_code=429, text="", is_reusable=True)

    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError) as exc:
            await fetch(_cfg(), client)

    assert exc.value.response.status_code == 429


@pytest.mark.asyncio
async def test_non_json_error_page_yields_no_items(httpx_mock):
    _add_search(httpx_mock, text="<html><body>bad gateway</body></html>", is_reusable=True)

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(), client)

    assert items == []
