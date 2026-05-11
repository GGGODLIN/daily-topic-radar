"""SQLite layer for items + fetch_runs."""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from social_info._time import utcnow

TAIPEI = timezone(timedelta(hours=8))

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
    also_appeared_in TEXT,
    comments_json TEXT
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
    error TEXT,
    error_class TEXT,
    attempts INTEGER
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
        existing_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(fetch_runs)")
        }
        if "error_class" not in existing_cols:
            self.conn.execute("ALTER TABLE fetch_runs ADD COLUMN error_class TEXT")
        if "attempts" not in existing_cols:
            self.conn.execute("ALTER TABLE fetch_runs ADD COLUMN attempts INTEGER")

        existing_item_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(items)")
        }
        if "comments_json" not in existing_item_cols:
            self.conn.execute("ALTER TABLE items ADD COLUMN comments_json TEXT")

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
        error_class: str = "",
        attempts: int = 1,
    ) -> None:
        self.conn.execute(
            "INSERT INTO fetch_runs "
            "(source, started_at, ended_at, status, items_fetched, error, error_class, attempts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source,
                started_at.isoformat(),
                ended_at.isoformat(),
                status,
                items_fetched,
                error,
                error_class,
                attempts,
            ),
        )
        self.conn.commit()

    def items_for_date(self, date: str) -> list[dict[str, Any]]:
        """Return items fetched during the given Asia/Taipei date (YYYY-MM-DD).

        fetched_at is stored as naive UTC, so we translate the Taipei date to
        a UTC half-open range. Without this, same-day re-runs at different wall
        clock times yield different batches: launchd at 06:05 CST writes rows
        with UTC date = previous day; a manual re-run after 08:00 CST queries
        UTC = today and gets nothing.
        """
        day_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=TAIPEI)
        start_utc = day_start.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
        end_utc = (
            (day_start + timedelta(days=1))
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
            .isoformat()
        )
        cur = self.conn.execute(
            "SELECT * FROM items WHERE fetched_at >= ? AND fetched_at < ? "
            "ORDER BY posted_at DESC",
            (start_utc, end_utc),
        )
        return [dict(r) for r in cur]

    def last_failed_sources(self) -> list[str]:
        """Return source IDs whose most recent fetch_run was a failure.

        For each source, looks at its latest fetch_run row; if status != 'ok'
        the source ID is included. Sources that succeeded last (even if
        previously failed) are excluded.
        """
        cur = self.conn.execute(
            "SELECT source, status FROM fetch_runs "
            "WHERE id IN (SELECT MAX(id) FROM fetch_runs GROUP BY source) "
            "AND status != 'ok'"
        )
        return [r["source"] for r in cur]

    def current_known_issues(self) -> list[dict[str, Any]]:
        """For each source, return aggregated state if its last run was a failure.

        Returns a list of dicts: {source, last_error, last_error_class,
        last_attempts, last_failed_at, last_ok_at, consecutive_fail_runs}.
        Sources whose latest run succeeded are excluded.
        """
        cur = self.conn.execute(
            """
            SELECT source, status, error, error_class, attempts, started_at
            FROM fetch_runs
            WHERE id IN (SELECT MAX(id) FROM fetch_runs GROUP BY source)
            AND status != 'ok'
            """
        )
        issues: list[dict[str, Any]] = []
        for row in cur.fetchall():
            source = row["source"]
            last_ok = self.conn.execute(
                "SELECT MAX(started_at) AS ts FROM fetch_runs "
                "WHERE source = ? AND status = 'ok'",
                (source,),
            ).fetchone()
            last_ok_at = last_ok["ts"] if last_ok else None
            if last_ok_at:
                consecutive = self.conn.execute(
                    "SELECT COUNT(*) AS n FROM fetch_runs "
                    "WHERE source = ? AND started_at > ? AND status != 'ok'",
                    (source, last_ok_at),
                ).fetchone()["n"]
            else:
                consecutive = self.conn.execute(
                    "SELECT COUNT(*) AS n FROM fetch_runs "
                    "WHERE source = ? AND status != 'ok'",
                    (source,),
                ).fetchone()["n"]
            issues.append(
                {
                    "source": source,
                    "last_error": row["error"] or "",
                    "last_error_class": row["error_class"] or "",
                    "last_attempts": row["attempts"] or 1,
                    "last_failed_at": row["started_at"],
                    "last_ok_at": last_ok_at,
                    "consecutive_fail_runs": consecutive,
                }
            )
        return issues

    def recent_fetch_runs(self, days: int = 7) -> list[dict[str, Any]]:
        since = (utcnow() - timedelta(days=days)).isoformat()
        cur = self.conn.execute(
            "SELECT * FROM fetch_runs WHERE started_at >= ? ORDER BY started_at DESC",
            (since,),
        )
        return [dict(r) for r in cur]

    def close(self) -> None:
        self.conn.close()
