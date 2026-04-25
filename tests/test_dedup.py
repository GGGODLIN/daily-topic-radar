import hashlib
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from social_info.db import Database
from social_info.dedup import (
    Deduper,
    compute_item_id,
    compute_title_hash,
    normalize_title,
)
from social_info.fetchers.base import Item


def test_normalize_title_strips_punctuation_and_lowercases():
    assert normalize_title("OpenAI Releases GPT-5!") == "openai releases gpt 5"
    assert normalize_title("中文 標題（測試）") == "中文 標題 測試"
    assert normalize_title("  multiple   spaces  ") == "multiple spaces"


def test_normalize_title_handles_full_width_space():
    assert normalize_title("hello　world") == "hello world"


def test_compute_item_id_uses_canonical_url():
    item_id = compute_item_id("https://example.com/a")
    assert item_id == hashlib.sha1(b"https://example.com/a").hexdigest()


def test_compute_title_hash():
    h = compute_title_hash("OpenAI Releases GPT-5!")
    expected = hashlib.sha1(b"openai releases gpt 5").hexdigest()
    assert h == expected


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        d = Database(Path(tmp) / "test.db")
        d.init_schema()
        yield d
        d.close()


def _make_item(url, title, source="hn", handle="front_page", tier=1):
    return Item(
        title=title,
        url=url,
        canonical_url=url,
        source=source,
        source_handle=handle,
        source_tier=tier,
        posted_at=datetime(2026, 4, 26, 8, 0, 0),
        fetched_at=datetime(2026, 4, 26, 9, 0, 0),
    )


def test_l1_dedup_skips_seen_url(db):
    deduper = Deduper(db)
    item1 = _make_item("https://example.com/a", "Hello")
    new1 = deduper.process([item1])
    assert len(new1) == 1

    # Persist the result so the next call sees it in db
    from social_info.dedup import compute_item_id, compute_title_hash
    for it in new1:
        db.insert_item(it.to_db_row(
            item_id=compute_item_id(it.canonical_url),
            title_hash=compute_title_hash(it.title),
        ))

    item_dup = _make_item("https://example.com/a", "Hello")
    new2 = deduper.process([item_dup])
    assert len(new2) == 0


def test_l2_dedup_merges_same_title_keeps_higher_tier(db):
    deduper = Deduper(db)
    item_t2 = _make_item(
        "https://a.com/x", "OpenAI Releases GPT-5!", source="rss", handle="vb", tier=2
    )
    new1 = deduper.process([item_t2])
    for it in new1:
        db.insert_item(it.to_db_row(
            item_id=compute_item_id(it.canonical_url),
            title_hash=compute_title_hash(it.title),
        ))

    item_t1 = _make_item(
        "https://b.com/y", "openai releases GPT 5", source="x", handle="@sama", tier=1
    )
    new = deduper.process([item_t1])

    assert len(new) == 1
    assert new[0].source_tier == 1
    appeared = new[0].also_appeared_in
    assert any(a["source"] == "rss" for a in appeared)


def test_l2_dedup_lower_tier_arrival_merged_into_existing(db):
    deduper = Deduper(db)
    item_t1 = _make_item(
        "https://a.com/x", "OpenAI Releases GPT-5!", source="x", handle="@sama", tier=1
    )
    new1 = deduper.process([item_t1])
    assert len(new1) == 1
    for it in new1:
        db.insert_item(it.to_db_row(
            item_id=compute_item_id(it.canonical_url),
            title_hash=compute_title_hash(it.title),
        ))

    item_t2 = _make_item(
        "https://b.com/y", "OPENAI Releases gpt 5!!", source="rss", handle="vb", tier=2
    )
    new2 = deduper.process([item_t2])
    assert len(new2) == 0
