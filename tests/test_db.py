import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from social_info.db import Database


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        d = Database(Path(tmp) / "test.db")
        d.init_schema()
        yield d
        d.close()


def test_init_creates_tables(db):
    cur = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur}
    assert "items" in tables
    assert "fetch_runs" in tables


def test_insert_and_query_item(db):
    row = {
        "id": "abc",
        "url": "https://example.com/a",
        "canonical_url": "https://example.com/a",
        "title": "Hello",
        "title_hash": "h1",
        "source": "hn",
        "source_handle": "front_page",
        "source_tier": 1,
        "posted_at": "2026-04-26T08:00:00",
        "fetched_at": "2026-04-26T09:00:00",
        "author": "user",
        "excerpt": "summary",
        "language": "en",
        "engagement_json": json.dumps({"score": 100}),
        "also_appeared_in": "[]",
    }
    db.insert_item(row)

    assert db.has_item_id("abc") is True
    assert db.has_item_id("xyz") is False


def test_has_title_hash(db):
    row = {
        "id": "abc",
        "url": "u",
        "canonical_url": "u",
        "title": "T",
        "title_hash": "TITLEHASH",
        "source": "hn",
        "source_handle": "fp",
        "source_tier": 1,
        "posted_at": "2026-04-26T08:00:00",
        "fetched_at": "2026-04-26T09:00:00",
        "author": "",
        "excerpt": "",
        "language": "en",
        "engagement_json": "{}",
        "also_appeared_in": "[]",
    }
    db.insert_item(row)
    found = db.find_by_title_hash("TITLEHASH")
    assert found is not None
    assert found["id"] == "abc"


def test_log_fetch_run(db):
    db.log_fetch_run(
        source="hn",
        started_at=datetime(2026, 4, 26, 9, 0, 0),
        ended_at=datetime(2026, 4, 26, 9, 0, 5),
        status="ok",
        items_fetched=10,
        error="",
    )
    cur = db.conn.execute("SELECT source, status, items_fetched FROM fetch_runs")
    rows = [tuple(r) for r in cur]
    assert rows == [("hn", "ok", 10)]


def test_recent_fetch_runs(db):
    db.log_fetch_run(
        "hn",
        datetime(2026, 4, 25, 9, 0),
        datetime(2026, 4, 25, 9, 0, 5),
        "ok",
        10,
        "",
    )
    db.log_fetch_run(
        "hn",
        datetime(2026, 4, 26, 9, 0),
        datetime(2026, 4, 26, 9, 0, 5),
        "failed",
        0,
        "boom",
    )
    rows = db.recent_fetch_runs(days=7)
    assert len(rows) == 2
