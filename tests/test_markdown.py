from datetime import datetime

from social_info.fetchers.base import FetchResult, Item
from social_info.markdown import render_file, render_item


def _item(**overrides):
    base = dict(
        title="Cursor Composer ships parallel edits",
        url="https://example.com/post",
        canonical_url="https://example.com/post",
        source="x",
        source_handle="@karpathy",
        source_tier=1,
        posted_at=datetime(2026, 4, 26, 14, 23, 0),
        fetched_at=datetime(2026, 4, 26, 9, 0, 0),
        author="Andrej Karpathy",
        excerpt="A nice take on parallel composer edits...",
        language="en",
        engagement={"likes": 1234, "comments": 84},
    )
    base.update(overrides)
    return Item(**base)


def test_render_item_basic():
    out = render_item(_item())
    assert "[Cursor Composer ships parallel edits](https://example.com/post)" in out
    assert "x:@karpathy" in out
    assert "T1" in out
    assert "♥ 1234" in out
    assert "💬 84" in out
    assert "> A nice take on parallel composer edits..." in out
    assert out.endswith("---\n")


def test_render_item_no_engagement():
    out = render_item(_item(engagement={}))
    assert "♥" not in out


def test_render_item_with_also_appeared_in():
    item = _item()
    item.also_appeared_in = [
        {"source": "rss", "source_handle": "techcrunch_ai", "url": "https://tc.com/x"},
        {"source": "hn", "source_handle": "front_page", "url": "https://news.yc.com/i?id=123"},
    ]
    out = render_item(item)
    assert "also seen at" in out.lower()
    assert "techcrunch_ai" in out
    assert "front_page" in out


def test_render_file_groups_by_platform():
    items = [
        _item(source="x", source_handle="@karpathy"),
        _item(
            source="reddit",
            source_handle="r/LocalLLaMA",
            url="https://r.com/a",
            canonical_url="https://r.com/a",
        ),
        _item(
            source="hn",
            source_handle="front_page",
            url="https://news.yc.com/x",
            canonical_url="https://news.yc.com/x",
        ),
    ]
    failures: list[FetchResult] = []
    out = render_file(
        date="2026-04-26",
        generated_at=datetime(2026, 4, 26, 9, 0, 0),
        items=items,
        failures=failures,
    )
    assert "# AI Daily Digest — 2026-04-26" in out
    assert "## X / Twitter" in out
    assert "## Reddit" in out
    assert "## Hacker News" in out
    assert "total_items: 3" in out


def test_render_file_with_failures():
    out = render_file(
        date="2026-04-26",
        generated_at=datetime(2026, 4, 26, 9, 0, 0),
        items=[],
        failures=[
            FetchResult(source_id="dcard_engineer", ok=False, error="timeout after 30s"),
        ],
    )
    assert "sources_failed: 1" in out
    assert "dcard_engineer" in out
    assert "timeout after 30s" in out


def test_render_file_surfaces_stale_all_deduped_source():
    fetched = [
        _item(url=f"https://x.com/{i}", canonical_url=f"https://x.com/{i}")
        for i in range(3)
    ]
    out = render_file(
        date="2026-05-18",
        generated_at=datetime(2026, 5, 18, 6, 0, 0),
        items=[],
        failures=[],
        stale=[
            FetchResult(
                source_id="twitter_tier1", items=fetched, ok=True, net_new=0
            )
        ],
    )
    assert "sources_failed: 0" in out
    assert "stale" in out.lower()
    assert "twitter_tier1" in out
    assert "3" in out


def test_render_file_no_stale_line_when_none():
    out = render_file(
        date="2026-05-18",
        generated_at=datetime(2026, 5, 18, 6, 0, 0),
        items=[_item()],
        failures=[],
    )
    assert "stale" not in out.lower()


def test_github_search_items_render_in_github_trending_group():
    """github_search source should bucket into github_trending group per _group_key_for_source."""
    from social_info.markdown import _group_key_for_source, render_file

    assert _group_key_for_source("github_search", "query:foo", "en") == "github_trending"

    from social_info.fetchers.base import Item

    item = Item(
        title="mattpocock/skills",
        url="https://github.com/mattpocock/skills",
        canonical_url="https://github.com/mattpocock/skills",
        source="github_search",
        source_handle="query:skills+in:name+claude",
        source_tier=1,
        posted_at=datetime(2026, 7, 9, 0, 0, 0),
        fetched_at=datetime(2026, 7, 9, 0, 0, 0),
        engagement={"stars": 162000},
    )
    md = render_file(
        date="2026-07-09",
        generated_at=datetime(2026, 7, 9, 6, 0, 0),
        items=[item],
        failures=[],
    )
    assert "mattpocock/skills" in md, "github_search item silently dropped from output"
    assert "GitHub Trending" in md, "github_search item did not land in GitHub Trending section"
