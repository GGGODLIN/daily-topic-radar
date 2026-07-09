import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from social_info._time import utcnow
from social_info.db import Database
from social_info.dedup import Deduper, compute_item_id, compute_title_hash
from social_info.fetchers.base import Item
from social_info.pipeline import resolve_resurface_items, write_report


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        d = Database(Path(tmp) / "test.db")
        d.init_schema()
        yield d
        d.close()


def _make_item(url, title):
    return Item(
        title=title, url=url, canonical_url=url,
        source="github_trending", source_handle="trending:python",
        source_tier=1,
        posted_at=datetime(2026, 7, 9, 0, 0, 0),
        fetched_at=datetime(2026, 7, 9, 0, 0, 0),
    )


def _insert_with_last_surfaced(db, item, last_surfaced_at):
    item_id = compute_item_id(item.canonical_url)
    row = item.to_db_row(item_id=item_id, title_hash=compute_title_hash(item.title))
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


def test_pipeline_resurface_updates_last_surfaced_at(db):
    item = _make_item("https://github.com/mattpocock/skills", "mattpocock/skills")
    old = (utcnow() - timedelta(days=40)).isoformat()
    item_id = _insert_with_last_surfaced(db, item, old)

    result = Deduper(db, resurface_days=30).process([item])
    assert item_id in result.resurface_ids

    db.update_last_surfaced_at(result.resurface_ids)

    updated = db.conn.execute(
        "SELECT last_surfaced_at FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    new_ts = datetime.fromisoformat(updated["last_surfaced_at"])
    assert (utcnow() - new_ts).total_seconds() < 5


def test_pipeline_resurface_skipped_when_within_n_days(db):
    item = _make_item("https://github.com/foo/bar", "foo/bar")
    recent = (utcnow() - timedelta(days=5)).isoformat()
    item_id = _insert_with_last_surfaced(db, item, recent)

    result = Deduper(db, resurface_days=30).process([item])
    assert result.resurface_ids == []

    before = db.conn.execute(
        "SELECT last_surfaced_at FROM items WHERE id = ?", (item_id,)
    ).fetchone()["last_surfaced_at"]
    assert before == recent


def test_resolve_resurface_items_rebuilds_full_item(db):
    item = _make_item("https://github.com/karpathy/skills", "karpathy/skills")
    old = (utcnow() - timedelta(days=35)).isoformat()
    _insert_with_last_surfaced(db, item, old)

    result = Deduper(db, resurface_days=30).process([item])
    resurfaced = resolve_resurface_items(db, result.resurface_ids)

    assert len(resurfaced) == 1
    assert resurfaced[0].url == "https://github.com/karpathy/skills"
    assert resurfaced[0].title == "karpathy/skills"
    assert resurfaced[0].source == "github_trending"


def test_write_report_marks_resurface_items_with_marker(db, tmp_path):
    item = _make_item("https://github.com/mattpocock/skills", "mattpocock/skills")
    old = (utcnow() - timedelta(days=35)).isoformat()
    _insert_with_last_surfaced(db, item, old)

    result = Deduper(db, resurface_days=30).process([item])
    resurfaced = resolve_resurface_items(db, result.resurface_ids)
    db.update_last_surfaced_at(result.resurface_ids)

    out_path = write_report(
        new_items=[],
        failures=[],
        out_dir=tmp_path,
        date="2026-07-09",
        generated_at=datetime(2026, 7, 9, 6, 0, 0),
        resurface_items=resurfaced,
    )

    content = out_path.read_text(encoding="utf-8")
    assert "🔁 [mattpocock/skills]" in content
    assert "total_items: 1" in content


def test_write_report_without_resurface_items_unmarked(db, tmp_path):
    item = _make_item("https://github.com/foo/bar", "foo/bar")

    out_path = write_report(
        new_items=[item],
        failures=[],
        out_dir=tmp_path,
        date="2026-07-09",
        generated_at=datetime(2026, 7, 9, 6, 0, 0),
    )

    content = out_path.read_text(encoding="utf-8")
    assert "🔁" not in content
    assert "[foo/bar]" in content
