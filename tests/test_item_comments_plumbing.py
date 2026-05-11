"""Plumbing tests for the Item.comments field, DB migration, and markdown render."""
import json
import sqlite3
from datetime import datetime

import pytest

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
