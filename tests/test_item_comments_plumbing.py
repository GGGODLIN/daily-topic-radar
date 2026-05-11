"""Plumbing tests for the Item.comments field, DB migration, and markdown render."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from social_info.db import Database
from social_info.fetchers.base import Item


def _sample_item(**overrides) -> Item:
    base = dict(
        title="t",
        url="https://example.com/x",
        canonical_url="https://example.com/x",
        source="hn",
        source_handle="front_page",
        source_tier=1,
        posted_at=datetime(2026, 5, 11, 12, 0),
        fetched_at=datetime(2026, 5, 11, 12, 0),
    )
    base.update(overrides)
    return Item(**base)


def test_item_default_comments_is_empty_list():
    item = _sample_item()
    assert item.comments == []


def test_item_to_db_row_serializes_comments_as_json():
    comments = [
        {"author": "alice", "text": "hello", "posted_at": "2026-05-11T11:00:00"},
        {"author": "bob", "text": "world", "posted_at": "2026-05-11T11:05:00"},
    ]
    item = _sample_item(comments=comments)
    row = item.to_db_row(item_id="abc", title_hash="xyz")
    assert "comments_json" in row
    assert json.loads(row["comments_json"]) == comments


def test_item_to_db_row_empty_comments_serializes_to_empty_list():
    item = _sample_item()
    row = item.to_db_row(item_id="abc", title_hash="xyz")
    assert row["comments_json"] == "[]"


def test_init_schema_creates_comments_json_on_fresh_db(tmp_path: Path):
    db = Database(tmp_path / "fresh.db")
    db.init_schema()
    cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(items)")}
    assert "comments_json" in cols
    db.close()


def test_init_schema_migrates_existing_db_without_comments_json(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(str(db_path))
    legacy.executescript(
        """
        CREATE TABLE items (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            title TEXT NOT NULL,
            title_hash TEXT NOT NULL,
            source TEXT NOT NULL,
            source_handle TEXT,
            source_tier INTEGER,
            posted_at TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            author TEXT,
            excerpt TEXT,
            language TEXT,
            engagement_json TEXT,
            also_appeared_in TEXT
        );
        CREATE TABLE fetch_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            status TEXT,
            items_fetched INTEGER,
            error TEXT,
            error_class TEXT,
            attempts INTEGER
        );
        """
    )
    legacy.commit()
    legacy.close()

    db = Database(db_path)
    db.init_schema()
    cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(items)")}
    assert "comments_json" in cols
    db.close()


def test_init_schema_is_idempotent_with_comments_json(tmp_path: Path):
    db = Database(tmp_path / "twice.db")
    db.init_schema()
    db.init_schema()
    cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(items)")}
    assert "comments_json" in cols
    db.close()
