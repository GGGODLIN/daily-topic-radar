import hashlib
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from social_info._time import utcnow
from social_info.db import Database
from social_info.dedup import (
    DedupResult,
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
    result1 = deduper.process([item1])
    assert len(result1.new_items) == 1

    # Persist the result so the next call sees it in db
    for it in result1.new_items:
        db.insert_item(it.to_db_row(
            item_id=compute_item_id(it.canonical_url),
            title_hash=compute_title_hash(it.title),
        ))

    item_dup = _make_item("https://example.com/a", "Hello")
    result2 = deduper.process([item_dup])
    assert len(result2.new_items) == 0


def test_l2_dedup_merges_same_title_keeps_higher_tier(db):
    deduper = Deduper(db)
    item_t2 = _make_item(
        "https://a.com/x", "OpenAI Releases GPT-5!", source="rss", handle="vb", tier=2
    )
    result1 = deduper.process([item_t2])
    for it in result1.new_items:
        db.insert_item(it.to_db_row(
            item_id=compute_item_id(it.canonical_url),
            title_hash=compute_title_hash(it.title),
        ))

    item_t1 = _make_item(
        "https://b.com/y", "openai releases GPT 5", source="x", handle="@sama", tier=1
    )
    result = deduper.process([item_t1])

    assert len(result.new_items) == 1
    assert result.new_items[0].source_tier == 1
    appeared = result.new_items[0].also_appeared_in
    assert any(a["source"] == "rss" for a in appeared)


def test_l2_dedup_lower_tier_arrival_merged_into_existing(db):
    deduper = Deduper(db)
    item_t1 = _make_item(
        "https://a.com/x", "OpenAI Releases GPT-5!", source="x", handle="@sama", tier=1
    )
    result1 = deduper.process([item_t1])
    assert len(result1.new_items) == 1
    for it in result1.new_items:
        db.insert_item(it.to_db_row(
            item_id=compute_item_id(it.canonical_url),
            title_hash=compute_title_hash(it.title),
        ))

    item_t2 = _make_item(
        "https://b.com/y", "OPENAI Releases gpt 5!!", source="rss", handle="vb", tier=2
    )
    result2 = deduper.process([item_t2])
    assert len(result2.new_items) == 0


def _insert_with_last_surfaced(db, item, last_surfaced_at, title_hash=None):
    item_id = compute_item_id(item.canonical_url)
    title_hash = title_hash if title_hash is not None else compute_title_hash(item.title)
    row = item.to_db_row(item_id=item_id, title_hash=title_hash)
    row["last_surfaced_at"] = last_surfaced_at
    db.conn.execute(
        "INSERT INTO items (id, url, canonical_url, title, title_hash, "
        "source, source_handle, source_tier, posted_at, fetched_at, "
        "language, engagement_json, also_appeared_in, last_surfaced_at) "
        "VALUES (:id, :url, :canonical_url, :title, :title_hash, :source, "
        ":source_handle, :source_tier, :posted_at, :fetched_at, :language, "
        ":engagement_json, :also_appeared_in, :last_surfaced_at)",
        row,
    )
    db.conn.commit()
    return item_id


def test_dedup_case_1_all_new_item(db):
    item = _make_item("https://github.com/foo/bar", "foo/bar")
    result = Deduper(db).process([item])

    assert isinstance(result, DedupResult)
    assert len(result.new_items) == 1
    assert result.new_items[0].url == "https://github.com/foo/bar"
    assert result.resurface_ids == []


def test_dedup_case_2_l2_hit_lt_n_days_no_resurface(db):
    existing = _make_item(
        "https://a.com/x", "OpenAI Releases GPT-5!", source="rss", handle="vb", tier=2
    )
    _insert_with_last_surfaced(db, existing, (utcnow() - timedelta(days=5)).isoformat())

    incoming = _make_item(
        "https://b.com/y", "openai releases GPT 5", source="x", handle="@sama", tier=1
    )
    result = Deduper(db, resurface_days=30).process([incoming])

    assert len(result.new_items) == 1
    assert result.new_items[0].source_tier == 1
    assert result.resurface_ids == []


def test_dedup_case_3_l2_hit_gte_n_days_resurfaces(db):
    existing = _make_item(
        "https://a.com/x", "OpenAI Releases GPT-5!", source="rss", handle="vb", tier=2
    )
    existing_id = _insert_with_last_surfaced(
        db, existing, (utcnow() - timedelta(days=40)).isoformat()
    )

    incoming = _make_item(
        "https://b.com/y", "openai releases GPT 5", source="x", handle="@sama", tier=1
    )
    result = Deduper(db, resurface_days=30).process([incoming])

    assert len(result.new_items) == 1
    assert result.new_items[0].source_tier == 1
    assert result.resurface_ids == [existing_id]


def test_dedup_case_4_l1_hit_lt_n_days_skipped(db):
    item = _make_item("https://github.com/foo/bar", "foo/bar")
    _insert_with_last_surfaced(db, item, (utcnow() - timedelta(days=5)).isoformat())

    result = Deduper(db).process([item])
    assert result.new_items == []
    assert result.resurface_ids == []


def test_dedup_case_5_l1_hit_gte_n_days_resurfaces(db):
    item = _make_item("https://github.com/foo/bar", "foo/bar")
    item_id = _insert_with_last_surfaced(
        db, item, (utcnow() - timedelta(days=35)).isoformat()
    )

    result = Deduper(db, resurface_days=30).process([item])
    assert result.new_items == []
    assert result.resurface_ids == [item_id]
