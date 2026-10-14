import asyncio
from datetime import datetime

from social_info.config import Config, SourceConfig
from social_info.db import Database
from social_info.fetchers.base import Item
from social_info.observation_pool import ObservationPool
from social_info.pipeline import FETCHER_REGISTRY, run_pipeline


def test_independent_collector_reads_this_runs_shared_observations(tmp_path, monkeypatch):
    db = Database(tmp_path / "state.db")
    db.init_schema()
    pool = ObservationPool(tmp_path / "pool.db")
    calls = []
    now = datetime(2026, 10, 14, 1)

    async def regular(source, http):
        calls.append("regular")
        return [Item(
            title="Shared post", url="https://x.com/test/status/1",
            canonical_url="https://x.com/test/status/1", source="x",
            source_handle="@test", source_tier=1, posted_at=now,
            fetched_at=now, content="Shared full text",
        )]

    async def independent(source, http):
        calls.append("independent")
        row = pool.conn.execute("SELECT content FROM observations").fetchone()
        assert row["content"] == "Shared full text"
        return []

    monkeypatch.setitem(FETCHER_REGISTRY, "twitter", regular)
    monkeypatch.setitem(FETCHER_REGISTRY, "new_term_radar", independent)
    config = Config(defaults={}, sources=[
        SourceConfig("free_radar", "new_term_radar", True, 2),
        SourceConfig("twitter", "twitter", True, 1),
    ])
    asyncio.run(run_pipeline(config, db, pool=pool))
    assert calls == ["regular", "independent"]
    assert pool.conn.execute("SELECT count(*) FROM source_runs").fetchone()[0] == 2
    pool.close()
    db.close()


def test_dry_run_does_not_update_independent_snapshots(tmp_path, monkeypatch):
    db = Database(tmp_path / "state.db")
    db.init_schema()

    async def prohibited(source, http):
        raise AssertionError("dry-run must not run a stateful collector")

    monkeypatch.setitem(FETCHER_REGISTRY, "new_term_radar", prohibited)
    config = Config(defaults={}, sources=[SourceConfig("free_radar", "new_term_radar", True, 2)])
    items, results, _ = asyncio.run(run_pipeline(config, db, dry_run=True))
    assert items == [] and results == []
    db.close()
