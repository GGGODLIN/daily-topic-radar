"""Chaos and regression tests for db, dedup, pipeline, known_issues, config, and topic_radar."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from social_info import __main__ as main_mod
from social_info import known_issues, pipeline
from social_info._time import utcnow
from social_info.config import Config, SourceConfig, load_config
from social_info.db import Database
from social_info.dedup import Deduper, compute_item_id, compute_title_hash
from social_info.fetchers.base import FetchResult, Item
from social_info.fetchers.linuxdo import LinuxDoFetchError
from social_info.observation_pool import ObservationPool
from social_info.topic_radar import (
    RadarHistory,
    _created,
    _github_breakout,
    _github_note,
    analyze,
    default_config,
    load_radar_config,
)


def _make_item(
    *,
    url: str,
    title: str,
    source: str = "rss",
    handle: str = "feed",
    tier: int = 2,
    canonical_url: str | None = None,
    posted_at: datetime | None = None,
    fetched_at: datetime | None = None,
    engagement: dict[str, int] | None = None,
) -> Item:
    now = utcnow()
    return Item(
        title=title,
        url=url,
        canonical_url=url if canonical_url is None else canonical_url,
        source=source,
        source_handle=handle,
        source_tier=tier,
        posted_at=posted_at or now,
        fetched_at=fetched_at or now,
        engagement=engagement or {},
    )


def _http_status_error(code: int) -> httpx.HTTPStatusError:
    response = MagicMock(spec=httpx.Response)
    response.status_code = code
    response.headers = httpx.Headers({})
    return httpx.HTTPStatusError("status", request=MagicMock(), response=response)


# 1. Transaction atomicity (pipeline.run_pipeline + db.py)
async def test_run_pipeline_rolls_back_atomically_on_insert_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "state.db"
    db = Database(db_path)
    db.init_schema()

    old_item = _make_item(
        url="https://old.example/post-1",
        title="Shared Benchmark Release",
        source="rss",
        handle="old_feed",
        tier=2,
    )
    old_id = compute_item_id(old_item.canonical_url)
    old_row = old_item.to_db_row(
        item_id=old_id,
        title_hash=compute_title_hash(old_item.title),
    )
    old_row["last_surfaced_at"] = utcnow().isoformat()
    db.insert_item(old_row)

    incoming = [
        _make_item(
            url="https://x.example/post-1",
            title="Shared Benchmark Release",
            source="x",
            handle="@reporter",
            tier=2,
        ),
        _make_item(
            url="https://new.example/item-1",
            title="Completely New Item One",
            source="hn",
            handle="front_page",
            tier=1,
        ),
        _make_item(
            url="https://new.example/item-2",
            title="Completely New Item Two",
            source="hn",
            handle="front_page",
            tier=1,
        ),
    ]

    async def fake_fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
        return incoming

    monkeypatch.setitem(pipeline.FETCHER_REGISTRY, "faketest_atomic", fake_fetch)

    orig_insert = db.insert_item
    insert_calls = 0

    def flaky_insert(row: dict) -> None:
        nonlocal insert_calls
        insert_calls += 1
        if insert_calls == 2:
            raise RuntimeError("disk full")
        orig_insert(row)

    monkeypatch.setattr(db, "insert_item", flaky_insert)

    config = Config(
        defaults={},
        sources=[SourceConfig(id="s_atomic", type="faketest_atomic", enabled=True, tier=1)],
    )

    with pytest.raises(RuntimeError, match="disk full"):
        await pipeline.run_pipeline(config, db)

    assert db.conn.execute("SELECT COUNT(*) FROM fetch_runs").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    persisted_old = db.conn.execute(
        "SELECT also_appeared_in FROM items WHERE id = ?", (old_id,)
    ).fetchone()
    assert json.loads(persisted_old["also_appeared_in"]) == []
    db.close()

    verify_db = Database(db_path)
    assert verify_db.conn.execute("SELECT COUNT(*) FROM fetch_runs").fetchone()[0] == 0
    assert verify_db.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    verify_old = verify_db.conn.execute(
        "SELECT also_appeared_in FROM items WHERE id = ?", (old_id,)
    ).fetchone()
    assert json.loads(verify_old["also_appeared_in"]) == []
    verify_db.close()


# 2. SQLite busy_timeout and lock contention tolerance (db.py + observation_pool.py)
def test_sqlite_busy_timeout_and_concurrent_lock_tolerance(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    pool_path = tmp_path / "pool.db"

    db = Database(db_path)
    db.init_schema()
    pool = ObservationPool(pool_path)

    try:
        db_busy = db.conn.execute("PRAGMA busy_timeout").fetchone()[0]
        pool_busy = pool.conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert db_busy >= 10000
        assert pool_busy >= 10000

        journal_mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
        assert journal_mode != "wal"
        assert not Path(f"{db_path}-wal").exists()
        assert not Path(f"{db_path}-shm").exists()

        # Hold an exclusive/immediate write lock on db_path briefly while Database writes.
        db_locked = threading.Event()

        def hold_db_lock() -> None:
            locker = sqlite3.connect(str(db_path), isolation_level=None)
            try:
                locker.execute("BEGIN IMMEDIATE")
                db_locked.set()
                time.sleep(0.2)
                locker.execute("COMMIT")
            finally:
                locker.close()

        t_db = threading.Thread(target=hold_db_lock)
        t_db.start()
        assert db_locked.wait(timeout=5.0)
        try:
            item = _make_item(url="https://lock.example/db", title="Lock Test DB")
            db.insert_item(
                item.to_db_row(
                    item_id=compute_item_id(item.canonical_url),
                    title_hash=compute_title_hash(item.title),
                )
            )
        finally:
            t_db.join()

        assert db.has_item_id(compute_item_id("https://lock.example/db"))

        # Hold an immediate write lock on pool_path briefly while ObservationPool writes.
        pool_locked = threading.Event()

        def hold_pool_lock() -> None:
            locker = sqlite3.connect(str(pool_path), isolation_level=None)
            try:
                locker.execute("BEGIN IMMEDIATE")
                pool_locked.set()
                time.sleep(0.2)
                locker.execute("COMMIT")
            finally:
                locker.close()

        t_pool = threading.Thread(target=hold_pool_lock)
        t_pool.start()
        assert pool_locked.wait(timeout=5.0)
        try:
            src = SourceConfig(id="s_lock", type="rss", enabled=True, tier=2)
            res = FetchResult(
                source_id="s_lock",
                items=[_make_item(url="https://lock.example/pool", title="Lock Test Pool")],
                ok=True,
                started_at=utcnow(),
                ended_at=utcnow(),
            )
            pool.record_result(src, res)
        finally:
            t_pool.join()

        assert pool.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    finally:
        db.close()
        pool.close()


# 3. Deduper boundary consistency (dedup.py)
def test_dedup_b4_two_t1_items_same_title_against_db_t2(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.db")
    db.init_schema()
    try:
        stored_t2 = _make_item(
            url="https://rss.example/story",
            title="Major Model Release Today",
            source="rss",
            handle="tech_rss",
            tier=2,
        )
        row = stored_t2.to_db_row(
            item_id=compute_item_id(stored_t2.canonical_url),
            title_hash=compute_title_hash(stored_t2.title),
        )
        row["last_surfaced_at"] = utcnow().isoformat()
        db.insert_item(row)

        t1_first = _make_item(
            url="https://x.example/status/1",
            title="Major Model Release Today",
            source="x",
            handle="@alice",
            tier=1,
        )
        t1_second = _make_item(
            url="https://hn.example/item/2",
            title="Major Model Release Today!",
            source="hn",
            handle="front_page",
            tier=1,
        )

        result = Deduper(db).process([t1_first, t1_second])
        assert len(result.new_items) == 1
        winner = result.new_items[0]
        appeared_sources = {entry["source"] for entry in winner.also_appeared_in}
        assert {"rss", "hn"} <= appeared_sources
    finally:
        db.close()


def test_dedup_b6_resurface_ids_no_duplicates_on_l2_batch(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.db")
    db.init_schema()
    try:
        stale_ts = (utcnow() - timedelta(days=45)).isoformat()
        stored = _make_item(
            url="https://orig.example/post",
            title="Evergreen Architecture Guide",
            source="hn",
            handle="front_page",
            tier=1,
        )
        stored_id = compute_item_id(stored.canonical_url)
        row = stored.to_db_row(
            item_id=stored_id,
            title_hash=compute_title_hash(stored.title),
        )
        row["last_surfaced_at"] = stale_ts
        db.insert_item(row)

        t2_a = _make_item(
            url="https://mirror-a.example/post",
            title="Evergreen Architecture Guide",
            source="rss_a",
            handle="feed_a",
            tier=2,
        )
        t2_b = _make_item(
            url="https://mirror-b.example/post",
            title="Evergreen Architecture Guide!",
            source="rss_b",
            handle="feed_b",
            tier=2,
        )

        result = Deduper(db, resurface_days=30).process([t2_a, t2_b])
        assert result.resurface_ids == [stored_id]
        assert len(result.resurface_ids) == 1
    finally:
        db.close()


def test_dedup_b7_batch_tier_upgrade_merges_subsequent_l1_collision(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.db")
    db.init_schema()
    try:
        item_a = _make_item(
            url="https://shared.example/u1",
            title="Same Headline Story",
            source="s1",
            handle="h1",
            tier=2,
        )
        item_b = _make_item(
            url="https://preferred.example/u2",
            title="Same Headline Story",
            source="s2",
            handle="h2",
            tier=1,
        )
        item_c = _make_item(
            url="https://shared.example/u1",
            title="Different Title On Same URL",
            source="s3",
            handle="h3",
            tier=2,
        )

        result = Deduper(db).process([item_a, item_b, item_c])
        assert len(result.new_items) == 1
        winner = result.new_items[0]
        assert winner.source == "s2"
        appeared_sources = {entry["source"] for entry in winner.also_appeared_in}
        assert {"s1", "s3"} <= appeared_sources
    finally:
        db.close()


def test_dedup_b8_parse_dt_converts_offset_to_naive_utc() -> None:
    parsed = Deduper._parse_dt("2026-11-21T08:00:00+08:00")
    assert parsed == datetime(2026, 11, 21, 0, 0, 0)


def test_dedup_b9_empty_canonical_url_does_not_swallow_unrelated_items(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.db")
    db.init_schema()
    try:
        item1 = _make_item(
            url="",
            canonical_url="",
            title="First Unrelated Post Without URL",
            source="source_alpha",
            handle="h_alpha",
            tier=2,
        )
        item2 = _make_item(
            url="",
            canonical_url="",
            title="Second Completely Different Post",
            source="source_beta",
            handle="h_beta",
            tier=2,
        )

        result = Deduper(db).process([item1, item2])
        assert len(result.new_items) == 2
        assert {it.title for it in result.new_items} == {
            "First Unrelated Post Without URL",
            "Second Completely Different Post",
        }
    finally:
        db.close()


# 4. pipeline.classify_error and non-default linuxdo source id
@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError("timed out"),
        TimeoutError(),
        httpx.TimeoutException("t"),
        _http_status_error(408),
        _http_status_error(425),
    ],
)
def test_classify_error_b15_timeouts_and_408_425(exc: BaseException) -> None:
    assert pipeline.classify_error(exc) == "transient"


async def test_run_pipeline_b16_non_default_linuxdo_id_keeps_partial_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = Database(tmp_path / "state.db")
    db.init_schema()
    pool = ObservationPool(tmp_path / "pool.db")

    kept_items = [
        _make_item(
            url="https://linux.do/t/topic/1",
            title="LinuxDo Kept One",
            source="linuxdo",
            handle="dev",
            tier=1,
        ),
        _make_item(
            url="https://linux.do/t/topic/2",
            title="LinuxDo Kept Two",
            source="linuxdo",
            handle="dev",
            tier=1,
        ),
    ]

    async def failing_linuxdo(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
        raise LinuxDoFetchError("rate limited (429)", kept=kept_items)

    monkeypatch.setitem(pipeline.FETCHER_REGISTRY, "linuxdo", failing_linuxdo)

    config = Config(
        defaults={},
        sources=[SourceConfig(id="linuxdo_alt", type="linuxdo", enabled=True, tier=1)],
    )

    try:
        new_items, results, _ = await pipeline.run_pipeline(config, db, pool=pool)
        assert len(results) == 1
        assert results[0].ok is False
        assert len(new_items) == len(kept_items)
        assert results[0].net_new == len(kept_items)
        assert pool.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == len(kept_items)
    finally:
        db.close()
        pool.close()


# 5. known_issues.py
def test_known_issues_b12_user_action_required_not_demoted_to_stable() -> None:
    issue = {
        "source": "reddit_localllama",
        "last_error": "HTTP 403 Forbidden",
        "last_error_class": "user_action_required",
        "last_attempts": 1,
        "last_failed_at": "2026-11-21T00:00:00+00:00",
        "last_ok_at": None,
        "consecutive_fail_runs": 10,
    }
    out = known_issues.render([issue], datetime(2026, 11, 21, 0, 0, 0))
    assert "## 🚨 User action required (1)" in out
    assert "Stable failures" not in out


def test_known_issues_b13_naive_utc_header_converted_to_taipei(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    naive_utc = datetime(2026, 5, 8, 1, 0, 0)
    for tz_name in ("Asia/Taipei", "UTC"):
        monkeypatch.setenv("TZ", tz_name)
        if hasattr(time, "tzset"):
            time.tzset()
        try:
            out = known_issues.render([], naive_utc)
            assert "2026-05-08 09:00 (Asia/Taipei)" in out
        finally:
            monkeypatch.delenv("TZ", raising=False)
            if hasattr(time, "tzset"):
                time.tzset()


# 6. config.py / topic_radar.py / __main__.py resilience and consistency
def test_config_b14_empty_and_null_yaml_and_non_dict_radar_yaml(tmp_path: Path) -> None:
    empty_yml = tmp_path / "empty.yml"
    empty_yml.write_text("", encoding="utf-8")
    try:
        cfg_empty = load_config(empty_yml)
        assert isinstance(cfg_empty, Config)
        assert cfg_empty.sources == []
    except ValueError:
        pass

    null_fields_yml = tmp_path / "nulls.yml"
    null_fields_yml.write_text("defaults: null\nsources: null\n", encoding="utf-8")
    try:
        cfg_nulls = load_config(null_fields_yml)
        assert isinstance(cfg_nulls, Config)
        assert cfg_nulls.defaults == {}
        assert cfg_nulls.sources == []
    except ValueError:
        pass

    list_radar_yml = tmp_path / "radar-list.yml"
    list_radar_yml.write_text("- a\n- b\n", encoding="utf-8")
    assert load_radar_config(list_radar_yml) == default_config()

    scalar_radar_yml = tmp_path / "radar-scalar.yml"
    scalar_radar_yml.write_text("123\n", encoding="utf-8")
    assert load_radar_config(scalar_radar_yml) == default_config()


def test_radar_b11_corrupted_json_and_free_radar_markdown(tmp_path: Path) -> None:
    trunc_path = tmp_path / "topic-radar-trunc.json"
    trunc_path.write_text('{"terms":', encoding="utf-8")
    h_trunc = RadarHistory(trunc_path)
    assert h_trunc.data == {"terms": {}, "leads": []}

    empty_path = tmp_path / "topic-radar-empty.json"
    empty_path.write_text("", encoding="utf-8")
    h_empty = RadarHistory(empty_path)
    assert h_empty.data == {"terms": {}, "leads": []}

    report_dir = tmp_path / "free-reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "2026-11-21.json").write_text('{"broken":', encoding="utf-8")
    cfg = Config(
        defaults={},
        sources=[
            SourceConfig(
                id="free_radar",
                type="new_term_radar",
                enabled=True,
                tier=2,
                params={"report_dir": str(report_dir)},
            )
        ],
    )
    assert main_mod._free_radar_markdown(cfg, "2026-11-21") == ""


def test_topic_radar_b8_aware_fetched_and_created_timestamps(tmp_path: Path) -> None:
    pool = ObservationPool(tmp_path / "pool.db")
    try:
        row = {
            "title": "org/aware-repo",
            "url": "https://github.com/org/aware-repo",
            "canonical_url": "https://github.com/org/aware-repo",
            "fetched_at": "2026-10-14T00:00:00+00:00",
            "engagement_json": json.dumps({"stars": 500}),
            "metadata_json": json.dumps({"created_at": "2026-10-13T08:00:00+08:00"}),
        }
        created = _created(row)
        assert created == datetime(2026, 10, 13, 0, 0, 0)
        assert _github_breakout(pool, [row], window_days=14, delta_min=400) is True
        notes = _github_note(pool, [row])
        assert len(notes) == 1
        assert notes[0]["age_days"] == 1.0
    finally:
        pool.close()


def test_topic_radar_b17_same_day_rerun_clears_excluded_leads_and_backfill_updates_earlier_term(
    tmp_path: Path,
) -> None:
    pool = ObservationPool(tmp_path / "pool.db")
    history = RadarHistory(tmp_path / "topic-radar.json")

    def record_x(source_id: str, handle: str, url: str, text: str, day_utc: datetime, likes: int = 10) -> None:
        it = _make_item(
            url=url,
            title=text,
            source="x",
            handle=handle,
            tier=1,
            posted_at=day_utc,
            fetched_at=day_utc,
            engagement={"likes": likes},
        )
        it.content = text
        pool.record_result(
            SourceConfig(id=source_id, type="twitter", enabled=True, tier=1),
            FetchResult(
                source_id=source_id,
                items=[it],
                ok=True,
                started_at=day_utc,
                ended_at=day_utc,
            ),
        )

    try:
        # Day 1 (2026-10-10 UTC): baseline term so later days are not baseline_only
        record_x("src_keep", "@alice", "https://x.com/a/1", "BaseLineV1 starts", datetime(2026, 10, 10, 2, 0))
        cfg = default_config()
        cfg["min_sources"] = 2
        cfg["min_likes"] = 100

        base_report = analyze(pool, cfg, history, "2026-10-10")
        assert base_report["baseline_only"] is True

        # Day 2 (2026-10-13): two sources mention NovelTool -> qualifies on first run
        record_x("src_keep", "@alice", "https://x.com/a/2", "NovelTool launches", datetime(2026, 10, 13, 2, 0))
        record_x("src_drop", "@bob", "https://x.com/b/2", "NovelTool benchmarks", datetime(2026, 10, 13, 2, 5))

        first_run = analyze(pool, cfg, history, "2026-10-13")
        assert any(lead["term"] == "noveltool" for lead in first_run["leads"])
        assert any(lead["term"] == "noveltool" for lead in history.leads_on("2026-10-13"))

        # Same-day rerun with src_drop excluded -> noveltool only has 1 account, no longer qualifies
        cfg_excluded = dict(cfg)
        cfg_excluded["exclude_source_ids"] = ["src_drop"]
        second_run = analyze(pool, cfg_excluded, history, "2026-10-13")
        assert all(lead["term"] != "noveltool" for lead in second_run["leads"])
        assert all(lead["term"] != "noveltool" for lead in history.leads_on("2026-10-13"))

        # Day 3 (2026-10-15): BackfillTerm first observed on 2026-10-15
        record_x("src_keep", "@alice", "https://x.com/a/3", "BackfillTerm appears", datetime(2026, 10, 15, 2, 0))
        analyze(pool, cfg, history, "2026-10-15")
        assert history.data["terms"]["backfillterm"] == "2026-10-15"

        # Backfill earlier date (2026-10-12) also saw BackfillTerm -> earliest date must move back
        record_x("src_keep", "@alice", "https://x.com/a/4", "BackfillTerm earlier", datetime(2026, 10, 12, 2, 0))
        analyze(pool, cfg, history, "2026-10-12")
        assert history.data["terms"]["backfillterm"] == "2026-10-12"
    finally:
        pool.close()


def test_db_nested_transaction_savepoint_and_compact_guard(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.db")
    db.init_schema()
    try:
        outer = _make_item(url="https://tx.example/outer", title="Outer")
        inner = _make_item(url="https://tx.example/inner", title="Inner")
        with db.transaction():
            db.insert_item(
                outer.to_db_row(
                    item_id=compute_item_id(outer.canonical_url),
                    title_hash=compute_title_hash(outer.title),
                )
            )
            with pytest.raises(RuntimeError, match="inner abort"), db.transaction():
                db.insert_item(
                    inner.to_db_row(
                        item_id=compute_item_id(inner.canonical_url),
                        title_hash=compute_title_hash(inner.title),
                    )
                )
                raise RuntimeError("inner abort")

            with pytest.raises(RuntimeError, match="active transaction"):
                db.compact()
            with pytest.raises(RuntimeError, match="active transaction"):
                db.init_schema()

        assert db.has_item_id(compute_item_id(outer.canonical_url))
        assert not db.has_item_id(compute_item_id(inner.canonical_url))
    finally:
        db.close()

