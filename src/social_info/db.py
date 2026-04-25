"""SQLite layer for items + fetch_runs."""
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
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
CREATE INDEX IF NOT EXISTS idx_items_title_hash ON items(title_hash);
CREATE INDEX IF NOT EXISTS idx_items_posted_at ON items(posted_at);

CREATE TABLE IF NOT EXISTS fetch_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT,
    items_fetched INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_fetch_runs_source ON fetch_runs(source);
CREATE INDEX IF NOT EXISTS idx_fetch_runs_started ON fetch_runs(started_at);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def has_item_id(self, item_id: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM items WHERE id = ? LIMIT 1", (item_id,))
        return cur.fetchone() is not None

    def find_by_title_hash(self, title_hash: str) -> dict[str, Any] | None:
        cur = self.conn.execute(
            "SELECT * FROM items WHERE title_hash = ? LIMIT 1", (title_hash,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def insert_item(self, row: dict[str, Any]) -> None:
        cols = ",".join(row.keys())
        placeholders = ",".join("?" for _ in row)
        self.conn.execute(
            f"INSERT OR IGNORE INTO items ({cols}) VALUES ({placeholders})",
            tuple(row.values()),
        )
        self.conn.commit()

    def update_also_appeared_in(self, item_id: str, also_appeared_in_json: str) -> None:
        self.conn.execute(
            "UPDATE items SET also_appeared_in = ? WHERE id = ?",
            (also_appeared_in_json, item_id),
        )
        self.conn.commit()

    def log_fetch_run(
        self,
        source: str,
        started_at: datetime,
        ended_at: datetime,
        status: str,
        items_fetched: int,
        error: str,
    ) -> None:
        self.conn.execute(
            "INSERT INTO fetch_runs (source, started_at, ended_at, status, items_fetched, error) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (source, started_at.isoformat(), ended_at.isoformat(), status, items_fetched, error),
        )
        self.conn.commit()

    def recent_fetch_runs(self, days: int = 7) -> list[dict[str, Any]]:
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cur = self.conn.execute(
            "SELECT * FROM fetch_runs WHERE started_at >= ? ORDER BY started_at DESC",
            (since,),
        )
        return [dict(r) for r in cur]

    def close(self) -> None:
        self.conn.close()
