import json
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from social_info._time import utcnow
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


def test_items_for_date_uses_taipei_range(db):
    def _row(item_id: str, fetched_at: str) -> dict:
        return {
            "id": item_id,
            "url": f"https://example.com/{item_id}",
            "canonical_url": f"https://example.com/{item_id}",
            "title": item_id,
            "title_hash": item_id,
            "source": "hn",
            "source_handle": "fp",
            "source_tier": 1,
            "posted_at": fetched_at,
            "fetched_at": fetched_at,
            "author": "",
            "excerpt": "",
            "language": "en",
            "engagement_json": "{}",
            "also_appeared_in": "[]",
        }

    db.insert_item(_row("before", "2026-04-29T15:59:59"))
    db.insert_item(_row("start_boundary", "2026-04-29T16:00:00"))
    db.insert_item(_row("launchd_morning", "2026-04-29T22:05:00"))
    db.insert_item(_row("end_boundary_in", "2026-04-30T15:59:59"))
    db.insert_item(_row("end_boundary_out", "2026-04-30T16:00:00"))

    rows = db.items_for_date("2026-04-30")
    ids = {r["id"] for r in rows}
    assert ids == {"start_boundary", "launchd_morning", "end_boundary_in"}


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


def test_log_fetch_run_records_net_new(db):
    db.log_fetch_run(
        source="x_tier1",
        started_at=datetime(2026, 5, 18, 6, 0, 0),
        ended_at=datetime(2026, 5, 18, 6, 0, 5),
        status="ok",
        items_fetched=106,
        error="",
        net_new=0,
    )
    cur = db.conn.execute(
        "SELECT items_fetched, net_new FROM fetch_runs WHERE source='x_tier1'"
    )
    assert tuple(cur.fetchone()) == (106, 0)


def test_log_fetch_run_net_new_defaults_null(db):
    db.log_fetch_run(
        source="hn",
        started_at=datetime(2026, 5, 18, 6, 0, 0),
        ended_at=datetime(2026, 5, 18, 6, 0, 5),
        status="ok",
        items_fetched=10,
        error="",
    )
    cur = db.conn.execute("SELECT net_new FROM fetch_runs WHERE source='hn'")
    assert cur.fetchone()[0] is None


def test_init_schema_adds_net_new_to_legacy_fetch_runs():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "legacy.db"
        legacy = sqlite3.connect(str(path))
        legacy.execute(
            "CREATE TABLE fetch_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, "
            "status TEXT, items_fetched INTEGER, error TEXT)"
        )
        legacy.commit()
        legacy.close()

        d = Database(path)
        d.init_schema()
        cols = {r["name"] for r in d.conn.execute("PRAGMA table_info(fetch_runs)")}
        d.close()
        assert "net_new" in cols


def test_last_failed_sources_returns_only_currently_failing(db):
    # source A: failed then succeeded — should NOT be returned
    db.log_fetch_run(
        "source_a", datetime(2026, 4, 25, 9, 0), datetime(2026, 4, 25, 9, 0, 5),
        "failed", 0, "boom",
    )
    db.log_fetch_run(
        "source_a", datetime(2026, 4, 26, 9, 0), datetime(2026, 4, 26, 9, 0, 5),
        "ok", 10, "",
    )
    # source B: succeeded then failed — SHOULD be returned (latest is failed)
    db.log_fetch_run(
        "source_b", datetime(2026, 4, 25, 9, 0), datetime(2026, 4, 25, 9, 0, 5),
        "ok", 5, "",
    )
    db.log_fetch_run(
        "source_b", datetime(2026, 4, 26, 9, 0), datetime(2026, 4, 26, 9, 0, 5),
        "failed", 0, "503",
    )
    # source C: only ever ok — should NOT be returned
    db.log_fetch_run(
        "source_c", datetime(2026, 4, 26, 9, 0), datetime(2026, 4, 26, 9, 0, 5),
        "ok", 3, "",
    )
    failed = db.last_failed_sources()
    assert failed == ["source_b"]


def test_items_schema_has_last_surfaced_at(db):
    cols = {row["name"] for row in db.conn.execute("PRAGMA table_info(items)")}
    assert "last_surfaced_at" in cols, \
        "items table missing last_surfaced_at column (Phase 2 migration missing)"


def test_migration_backfills_last_surfaced_at_from_posted_at():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"

        legacy = sqlite3.connect(str(db_path))
        legacy.execute(
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
            )
            """
        )
        legacy.execute(
            "INSERT INTO items (id, url, canonical_url, title, title_hash, "
            "source, source_handle, source_tier, posted_at, fetched_at) "
            "VALUES ('id1', 'https://x/', 'https://x/', 't', 'th', 's', 'h', 1, "
            "'2026-06-01T00:00:00', '2026-06-01T00:00:00')"
        )
        legacy.commit()
        legacy.close()

        d = Database(db_path)
        d.init_schema()

        row = d.conn.execute(
            "SELECT last_surfaced_at, posted_at FROM items WHERE id = 'id1'"
        ).fetchone()
        d.close()
        assert row["last_surfaced_at"] == row["posted_at"], \
            "migration should backfill last_surfaced_at from posted_at"


def test_recent_fetch_runs(db):
    now = utcnow()
    db.log_fetch_run(
        "hn",
        now - timedelta(days=2),
        now - timedelta(days=2, seconds=-5),
        "ok",
        10,
        "",
    )
    db.log_fetch_run(
        "hn",
        now - timedelta(days=1),
        now - timedelta(days=1, seconds=-5),
        "failed",
        0,
        "boom",
    )
    rows = db.recent_fetch_runs(days=7)
    assert len(rows) == 2


def test_compact_prunes_expired_rows_and_trims_old_excerpts(db):
    now = datetime(2026, 11, 5, 6, 0, 0)
    long_text = "x" * 500

    def _make_item(item_id: str, fetched_days_ago: int, surfaced_days_ago: int | None = None) -> dict:
        fetched = (now - timedelta(days=fetched_days_ago)).isoformat()
        surfaced = (
            (now - timedelta(days=surfaced_days_ago)).isoformat()
            if surfaced_days_ago is not None
            else fetched
        )
        return {
            "id": item_id,
            "url": f"https://example.com/{item_id}",
            "canonical_url": f"https://example.com/{item_id}",
            "title": f"title-{item_id}",
            "title_hash": f"hash-{item_id}",
            "source": "linuxdo",
            "source_handle": "latest",
            "source_tier": 2,
            "posted_at": fetched,
            "fetched_at": fetched,
            "author": "alice",
            "excerpt": long_text,
            "language": "zh",
            "engagement_json": "{}",
            "also_appeared_in": "[]",
            "comments_json": '[{"author":"bob","body":"hi"}]',
            "last_surfaced_at": surfaced,
        }

    db.insert_item(_make_item("fresh", fetched_days_ago=1))
    db.insert_item(_make_item("mid_age", fetched_days_ago=10))
    db.insert_item(_make_item("old_but_resurfaced", fetched_days_ago=60, surfaced_days_ago=2))
    db.insert_item(_make_item("expired", fetched_days_ago=46))

    db.log_fetch_run("hn", now - timedelta(days=60), now - timedelta(days=60), "ok", 5, "")
    db.log_fetch_run("hn", now - timedelta(days=1), now - timedelta(days=1), "ok", 8, "")
    db.log_fetch_run("legacy_src", now - timedelta(days=90), now - timedelta(days=90), "ok", 3, "")
    db.log_fetch_run("legacy_src", now - timedelta(days=10), now - timedelta(days=10), "failed", 0, "err")

    stats = db.compact(retain_days=45, full_excerpt_days=3, max_excerpt_chars=200, now=now)
    assert stats["deleted_items"] == 1
    assert stats["trimmed_items"] == 1
    assert stats["deleted_runs"] == 1

    assert db.has_item_id("expired") is False
    assert db.has_item_id("fresh") is True
    assert db.has_item_id("mid_age") is True
    assert db.has_item_id("old_but_resurfaced") is True

    fresh_row = db.get_items_by_ids(["fresh"])[0]
    assert len(fresh_row["excerpt"]) == 500
    assert fresh_row["comments_json"] != "[]"

    mid_row = db.get_items_by_ids(["mid_age"])[0]
    assert len(mid_row["excerpt"]) == 200
    assert mid_row["comments_json"] == "[]"

    resurf_row = db.get_items_by_ids(["old_but_resurfaced"])[0]
    assert len(resurf_row["excerpt"]) == 500

    issues = {i["source"]: i for i in db.current_known_issues()}
    assert "legacy_src" in issues
    assert issues["legacy_src"]["last_ok_at"] is not None


def test_items_schema_creates_fetched_at_index(db):
    indexes = {row["name"] for row in db.conn.execute("PRAGMA index_list(items)")}
    assert "idx_items_fetched_at" in indexes

