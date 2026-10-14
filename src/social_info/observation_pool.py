"""Local observation pool. Separate from tracked state.db.

Successful items are stored before dedup. The same
(source_id, canonical_url, fetched_at) row is written once. Full content
stays here; state.db keeps the existing excerpt-only row.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from social_info.config import Config, SourceConfig
from social_info.db import Database
from social_info.fetchers.base import FetchResult, Item

# state.db stores the report source name. Radar config uses fetcher types.
_SOURCE_KIND = {
    "x": "twitter",
    "hn": "hn_algolia",
    "rss": "rss",
    "rsshub": "rsshub",
    "huggingface": "huggingface",
    "github_search": "github_search",
    "github_trending": "github_trending",
    "trendshift": "trendshift",
    "skills_sh": "skills_sh",
    "product_hunt": "product_hunt",
    "linuxdo": "linuxdo",
    "reddit": "reddit",
    "threads": "threads",
    "v2ex": "v2ex",
    "wewe_rss": "wewe_rss",
}


def tracked_source_type(source: str) -> str:
    return _SOURCE_KIND.get(source, "")


SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    source_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    source_handle TEXT,
    source_tier INTEGER,
    posted_at TEXT NOT NULL,
    author TEXT,
    excerpt TEXT,
    content TEXT,
    content_status TEXT NOT NULL,
    language TEXT,
    engagement_json TEXT,
    metadata_json TEXT,
    partial INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source_id, canonical_url, fetched_at)
);
CREATE INDEX IF NOT EXISTS idx_obs_fetched ON observations(fetched_at);
CREATE INDEX IF NOT EXISTS idx_obs_url ON observations(canonical_url);

CREATE TABLE IF NOT EXISTS source_runs (
    source_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    source_type TEXT NOT NULL,
    status TEXT NOT NULL,
    items_fetched INTEGER NOT NULL,
    partial INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source_id, fetched_at)
);
"""


def _handle_matches(source_cfg: SourceConfig, handle: str) -> bool:
    """Match the handle shape the live fetcher writes. Ambiguous rows stay unknown."""
    bare = handle.lstrip("@").lower()
    handles = source_cfg.params.get("handles") or []
    if source_cfg.id == handle or bare in {str(item).lstrip("@").lower() for item in handles}:
        return True
    if source_cfg.type == "huggingface" and handle == f"trending:{source_cfg.params.get('category', 'models')}":
        return True
    if source_cfg.type == "github_trending":
        languages = source_cfg.params.get("languages") or [""]
        periods = source_cfg.params.get("since") or ["daily"]
        if isinstance(periods, str):
            periods = [periods]
        expected = {
            f"trending:{lang or 'all'}:{period}"
            for lang in languages for period in periods
        }
        return handle in expected
    if source_cfg.type == "github_search" and handle.startswith("query:"):
        queries = source_cfg.params.get("queries") or []
        return any(handle == f"query:{str(query)[:50]}" for query in queries)
    if source_cfg.type == "trendshift" and handle.startswith("rising:rank-") and handle.removeprefix("rising:rank-").isdigit():
        return True
    if source_cfg.type == "skills_sh":
        board, _, rank = handle.partition(":rank-")
        return board in {"trending", "hot"} and rank.isdigit()
    return False


def _seed_identity(row: dict[str, Any], config: Config | None) -> tuple[str, str, bool]:
    source = row.get("source") or ""
    handle = row.get("source_handle") or ""
    kind = tracked_source_type(source)
    matches = []
    if config is not None and kind:
        matches = [
            source_cfg for source_cfg in config.sources
            if source_cfg.type == kind and _handle_matches(source_cfg, handle)
        ]
    if len(matches) == 1:
        return matches[0].id, matches[0].type, True
    if kind:
        return "", kind, False
    return "", "", False


def content_status(item: Item) -> str:
    if item.content:
        return "content"
    if item.excerpt:
        return "excerpt"
    return "title"


class ObservationPool:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def record_result(self, source: SourceConfig, result: FetchResult) -> None:
        fetched_at = (result.ended_at or result.started_at).isoformat()
        partial = int(source.type == "linuxdo" and not result.ok and bool(result.items))
        if result.ok and result.items:
            status = "success"
        elif result.ok:
            status = "empty"
        else:
            status = "failed"
        self.conn.execute(
            "INSERT OR IGNORE INTO source_runs "
            "(source_id, fetched_at, source_type, status, items_fetched, partial) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (source.id, fetched_at, source.type, status, result.items_count(), partial),
        )
        if result.ok or partial:
            for item in result.items:
                self._insert_item(source, item, partial)
        self.conn.commit()

    def _insert_item(self, source: SourceConfig, item: Item, partial: int) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO observations ("
            "source_id, source_type, canonical_url, fetched_at, url, title, source, "
            "source_handle, source_tier, posted_at, author, excerpt, content, "
            "content_status, language, engagement_json, metadata_json, partial"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source.id,
                source.type,
                item.canonical_url,
                item.fetched_at.isoformat(),
                item.url,
                item.title,
                item.source,
                item.source_handle,
                item.source_tier,
                item.posted_at.isoformat(),
                item.author,
                item.excerpt,
                item.content,
                content_status(item),
                item.language,
                json.dumps(item.engagement),
                json.dumps(item.metadata),
                partial,
            ),
        )

    def seed_from_database(
        self, db: Database, date: str, config: Config | None = None,
    ) -> int:
        """Copy today's tracked rows. They only have excerpts, so content is marked legacy.

        source_id comes from the config entry whose type and handle match. A bare
        report source such as ``x`` is not a config id and is not stored as one.
        """
        rows = db.items_for_date(date)
        inserted = 0
        for row in rows:
            source_id, source_type, known = _seed_identity(row, config)
            if not known:
                source_type = "unknown"
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO observations ("
                "source_id, source_type, canonical_url, fetched_at, url, title, source, "
                "source_handle, source_tier, posted_at, author, excerpt, content, "
                "content_status, language, engagement_json, metadata_json, partial"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 'legacy_excerpt', ?, ?, ?, 0)",
                (
                    source_id,
                    source_type,
                    row["canonical_url"],
                    row["fetched_at"],
                    row["url"],
                    row["title"],
                    row["source"],
                    row.get("source_handle") or "",
                    row.get("source_tier") or 2,
                    row["posted_at"],
                    row.get("author") or "",
                    row.get("excerpt") or "",
                    row.get("language") or "en",
                    row.get("engagement_json") or "{}",
                    json.dumps({
                        "content_kind": "legacy_excerpt",
                        "seed_known": known,
                    }),
                ),
            )
            inserted += cur.rowcount
        self.conn.commit()
        return inserted

    def observations_between(self, start: str, end: str) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM observations WHERE fetched_at >= ? AND fetched_at < ? "
            "ORDER BY fetched_at, canonical_url",
            (start, end),
        )
        return [dict(row) for row in cur]

    def latest_per_url(self, before: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT * FROM observations WHERE fetched_at = ("
            "SELECT MAX(fetched_at) FROM observations o2 "
            "WHERE o2.canonical_url = observations.canonical_url"
        )
        params: tuple[str, ...] = ()
        if before:
            sql += " AND o2.fetched_at < ?"
            params = (before,)
        sql += ")"
        cur = self.conn.execute(sql, params)
        return [dict(row) for row in cur]

    def history_before(self, canonical_url: str, fetched_at: str) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM observations WHERE canonical_url = ? AND fetched_at < ? "
            "ORDER BY fetched_at",
            (canonical_url, fetched_at),
        )
        return [dict(row) for row in cur]

    def runs_between(self, start: str, end: str) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM source_runs WHERE fetched_at >= ? AND fetched_at < ? "
            "ORDER BY fetched_at, source_id",
            (start, end),
        )
        return [dict(row) for row in cur]

    def latest_content(self, canonical_url: str, start: str, end: str) -> dict[str, Any] | None:
        cur = self.conn.execute(
            "SELECT content, content_status, excerpt, metadata_json, source FROM observations "
            "WHERE canonical_url = ? AND fetched_at >= ? AND fetched_at < ? "
            "ORDER BY fetched_at DESC LIMIT 1",
            (canonical_url, start, end),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def pool_window(date: str) -> tuple[str, str]:
    """Naive-UTC day window. Pool timestamps are stored the same way as state.db."""
    day = datetime.strptime(date, "%Y-%m-%d")
    return day.isoformat(), (day + timedelta(days=1)).isoformat()
