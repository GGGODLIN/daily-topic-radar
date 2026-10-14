import json
from datetime import datetime
from pathlib import Path

from social_info.config import Config, SourceConfig
from social_info.db import Database
from social_info.fetchers.base import FetchResult, Item
from social_info.observation_pool import ObservationPool
from social_info.pipeline import run_pipeline
from social_info.topic_radar import (
    RadarHistory,
    analyze,
    base_name,
    load_radar_config,
)


def _item(url="https://x.com/a/status/1", **kw):
    base = dict(
        title="HyperFrames ships",
        url=url,
        canonical_url=url,
        source="x",
        source_handle="@ada",
        source_tier=1,
        posted_at=datetime(2026, 10, 13, 1, 0),
        fetched_at=datetime(2026, 10, 13, 0, 0),
        author="ada",
        excerpt="short",
        content="x" * 240,
        engagement={"likes": 120},
    )
    base.update(kw)
    return Item(**base)


def _source(sid="twitter_tier1", kind="twitter"):
    return SourceConfig(id=sid, type=kind, enabled=True, tier=1)


def test_long_content_stays_out_of_state_row():
    item = _item()
    row = item.to_db_row("id", "hash")
    assert "content" not in row
    assert len(item.content) > 200
    assert len(row["excerpt"]) < 200


def test_second_observation_and_idempotent_insert(tmp_path):
    pool = ObservationPool(tmp_path / "pool.db")
    result = FetchResult(
        source_id="twitter_tier1",
        items=[_item()],
        ok=True,
        started_at=datetime(2026, 10, 13, 0, 0),
        ended_at=datetime(2026, 10, 13, 0, 0),
    )
    pool.record_result(_source(), result)
    pool.record_result(_source(), result)
    later = _item(fetched_at=datetime(2026, 10, 14, 0, 0), engagement={"likes": 180})
    pool.record_result(_source(), FetchResult(
        source_id="twitter_tier1", items=[later], ok=True,
        started_at=later.fetched_at, ended_at=later.fetched_at,
    ))
    count = pool.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    assert count == 2
    history = pool.history_before(later.canonical_url, later.fetched_at.isoformat())
    assert json.loads(history[0]["engagement_json"])["likes"] == 120
    pool.close()


def test_failed_linuxdo_is_partial_without_error_text(tmp_path):
    pool = ObservationPool(tmp_path / "pool.db")
    result = FetchResult(
        source_id="linuxdo", items=[_item(source="linuxdo")], ok=False,
        error="token=secret", started_at=datetime(2026, 10, 13, 0, 0),
        ended_at=datetime(2026, 10, 13, 0, 0),
    )
    pool.record_result(_source("linuxdo", "linuxdo"), result)
    run = pool.conn.execute("SELECT status, partial FROM source_runs").fetchone()
    assert run["status"] == "failed" and run["partial"] == 1
    blob = Path(tmp_path / "pool.db").read_bytes()
    assert b"token=secret" not in blob
    pool.close()


def test_one_fetch_reaches_pool_and_state(tmp_path, monkeypatch):
    calls = {"n": 0}

    async def fake(source, http):
        calls["n"] += 1
        return [_item()]

    monkeypatch.setitem(
        __import__("social_info.pipeline", fromlist=["FETCHER_REGISTRY"]).FETCHER_REGISTRY,
        "twitter", fake,
    )
    db = Database(tmp_path / "state.db")
    db.init_schema()
    pool = ObservationPool(tmp_path / "pool.db")
    config = Config(defaults={}, sources=[_source()])
    import asyncio
    asyncio.run(run_pipeline(config, db, pool=pool))
    assert calls["n"] == 1
    assert pool.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    cols = {row["name"] for row in db.conn.execute("PRAGMA table_info(items)")}
    assert "content" not in cols
    pool.close()
    db.close()


def test_baseline_then_new_name_survives_rerun(tmp_path):
    pool = ObservationPool(tmp_path / "pool.db")
    history = RadarHistory(tmp_path / "topic-radar.json")
    cfg = load_radar_config(Path("topic-radar.yml"))
    old = _item(content="HyperFrames arrived", engagement={"likes": 10})
    pool.record_result(_source(), FetchResult(
        source_id="twitter_tier1", items=[old], ok=True,
        started_at=old.fetched_at, ended_at=old.fetched_at,
    ))
    first = analyze(pool, cfg, history, "2026-10-13")
    assert first["baseline_only"] is True
    assert first["leads"] == []
    new = _item(
        url="https://x.com/b/status/2", canonical_url="https://x.com/b/status/2",
        author="bea", source_handle="@bea",
        fetched_at=datetime(2026, 10, 14, 0, 0),
        posted_at=datetime(2026, 10, 14, 1, 0),
        content="Qwen3.8 Flash is the one", engagement={"likes": 150},
    )
    pool.record_result(_source("twitter_tier2"), FetchResult(
        source_id="twitter_tier2", items=[new], ok=True,
        started_at=new.fetched_at, ended_at=new.fetched_at,
    ))
    second = analyze(pool, cfg, RadarHistory(history.path), "2026-10-14")
    assert any(lead["term"] == "qwen3.8 flash" for lead in second["leads"])
    third = analyze(pool, cfg, RadarHistory(history.path), "2026-10-14")
    assert any(lead["term"] == "qwen3.8 flash" for lead in third["leads"])
    pool.close()


def test_hf_suffixes_share_one_base_name():
    assert base_name("org/Qwen-Image-2.1-Uncensored-GGUF") == "qwen image 2.1"
    assert base_name("other/Qwen-Image-2.1-27B-Instruct") == base_name(
        "org/Qwen-Image-2.1-Uncensored-GGUF"
    )


def test_failure_is_not_a_finished_baseline(tmp_path):
    pool = ObservationPool(tmp_path / "pool.db")
    started = datetime(2026, 10, 13, 0, 0)
    pool.record_result(_source(), FetchResult(
        source_id="twitter_tier1", items=[], ok=False, error="nope",
        started_at=started, ended_at=started,
    ))
    report = analyze(pool, load_radar_config(Path("topic-radar.yml")), RadarHistory(tmp_path / "h.json"), "2026-10-13")
    assert report["status"] == "no_data"
    assert report["baseline_only"] is False
    assert report["failed"]
    pool.close()


def test_seed_marks_legacy_excerpt(tmp_path):
    db = Database(tmp_path / "state.db")
    db.init_schema()
    db.insert_item({
        "id": "1", "url": "https://example.com/a", "canonical_url": "https://example.com/a",
        "title": "Old", "title_hash": "h", "source": "hn", "source_handle": "front_page",
        "source_tier": 1, "posted_at": "2026-10-13T01:00:00",
        "fetched_at": "2026-10-13T00:00:00", "author": "", "excerpt": "only excerpt",
        "language": "en", "engagement_json": "{}", "also_appeared_in": "[]",
    })
    pool = ObservationPool(tmp_path / "pool.db")
    assert pool.seed_from_database(db, "2026-10-13") == 1
    status = pool.conn.execute("SELECT content_status, content FROM observations").fetchone()
    assert status["content_status"] == "legacy_excerpt"
    assert status["content"] == ""
    pool.close()
    db.close()


def test_seed_matches_live_fetcher_handles(tmp_path):
    from social_info.config import Config, SourceConfig

    db = Database(tmp_path / "state.db")
    db.init_schema()
    samples = [
        ("huggingface", "trending:models", "huggingface_models"),
        ("huggingface", "trending:spaces", "huggingface_spaces"),
        ("github_trending", "trending:python:daily", "github_trending"),
        ("github_search", "query:topic:ai", "github_search"),
        ("trendshift", "rising:rank-3", "trendshift"),
        ("skills_sh", "hot:rank-2", "skills_sh"),
        ("github_trending", "trending:nope:daily", ""),
    ]
    config = Config(defaults={}, sources=[
        SourceConfig(id="huggingface_models", type="huggingface", enabled=True, tier=1, params={"category": "models"}),
        SourceConfig(id="huggingface_spaces", type="huggingface", enabled=True, tier=1, params={"category": "spaces"}),
        SourceConfig(id="github_trending", type="github_trending", enabled=True, tier=1, params={"languages": ["python"], "since": ["daily"]}),
        SourceConfig(id="github_search", type="github_search", enabled=True, tier=1, params={"queries": ["topic:ai"]}),
        SourceConfig(id="trendshift", type="trendshift", enabled=True, tier=1),
        SourceConfig(id="skills_sh", type="skills_sh", enabled=True, tier=1),
    ])
    for index, (source, handle, _expected) in enumerate(samples):
        db.insert_item({
            "id": str(index), "url": f"https://example.com/{index}",
            "canonical_url": f"https://example.com/{index}", "title": "t",
            "title_hash": f"h{index}", "source": source, "source_handle": handle,
            "source_tier": 1, "posted_at": "2026-10-13T01:00:00",
            "fetched_at": "2026-10-13T00:00:00", "author": "", "excerpt": "",
            "language": "en", "engagement_json": "{}", "also_appeared_in": "[]",
        })
    pool = ObservationPool(tmp_path / "pool.db")
    pool.seed_from_database(db, "2026-10-13", config)
    rows = pool.conn.execute("SELECT source_id, source_type FROM observations ORDER BY url").fetchall()
    assert [(row["source_id"], row["source_type"]) for row in rows] == [
        ("huggingface_models", "huggingface"),
        ("huggingface_spaces", "huggingface"),
        ("github_trending", "github_trending"),
        ("github_search", "github_search"),
        ("trendshift", "trendshift"),
        ("skills_sh", "skills_sh"),
        ("", "unknown"),
    ]
    assert pool.conn.execute("SELECT COUNT(*) FROM source_runs").fetchone()[0] == 0
    report = analyze(pool, {"types": ["huggingface"], "source_ids": [], "exclude_source_ids": []}, RadarHistory(tmp_path / "h.json"), "2026-10-13")
    text = __import__("social_info.topic_radar", fromlist=["render_radar_markdown"]).render_radar_markdown(report)
    assert "no_run_health" in text
    assert "failed sources: none" not in text
    pool.close()
    db.close()


def test_seed_maps_x_to_twitter_config_id(tmp_path):
    from social_info.config import Config, SourceConfig

    db = Database(tmp_path / "state.db")
    db.init_schema()
    db.insert_item({
        "id": "1", "url": "https://x.com/ada/status/1",
        "canonical_url": "https://x.com/ada/status/1",
        "title": "Old", "title_hash": "h", "source": "x",
        "source_handle": "@ada", "source_tier": 1,
        "posted_at": "2026-10-13T01:00:00", "fetched_at": "2026-10-13T00:00:00",
        "author": "Ada", "excerpt": "only excerpt", "language": "en",
        "engagement_json": "{}", "also_appeared_in": "[]",
    })
    config = Config(defaults={}, sources=[
        SourceConfig(id="twitter_tier1", type="twitter", enabled=True, tier=1, params={"handles": ["ada"]}),
    ])
    pool = ObservationPool(tmp_path / "pool.db")
    pool.seed_from_database(db, "2026-10-13", config)
    row = pool.conn.execute("SELECT source_id, source_type FROM observations").fetchone()
    assert row["source_id"] == "twitter_tier1"
    assert row["source_type"] == "twitter"
    pool.close()
    db.close()


def test_single_hf_model_is_a_lead_after_baseline(tmp_path):
    pool = ObservationPool(tmp_path / "pool.db")
    history = RadarHistory(tmp_path / "h.json")
    cfg = load_radar_config(Path("topic-radar.yml"))
    old = _item(source="huggingface", content="", excerpt="text-generation", fetched_at=datetime(2026, 10, 12, 0, 0))
    pool.record_result(_source("huggingface_models", "huggingface"), FetchResult(
        source_id="huggingface_models", items=[old], ok=True,
        started_at=old.fetched_at, ended_at=old.fetched_at,
    ))
    analyze(pool, cfg, history, "2026-10-12")
    new = _item(
        title="org/Qwen-Image-2.1-GGUF", url="https://huggingface.co/org/Qwen-Image-2.1-GGUF",
        canonical_url="https://huggingface.co/org/Qwen-Image-2.1-GGUF", source="huggingface",
        content="", excerpt="text-generation", metadata={"id": "org/Qwen-Image-2.1-GGUF"},
        fetched_at=datetime(2026, 10, 13, 0, 0), author="org",
    )
    pool.record_result(_source("huggingface_models", "huggingface"), FetchResult(
        source_id="huggingface_models", items=[new], ok=True,
        started_at=new.fetched_at, ended_at=new.fetched_at,
    ))
    report = analyze(pool, cfg, RadarHistory(history.path), "2026-10-13")
    assert any(lead["term"] == "qwen image 2.1" for lead in report["leads"])
    pool.close()


def test_latest_observation_supplies_likes(tmp_path):
    pool = ObservationPool(tmp_path / "pool.db")
    history = RadarHistory(tmp_path / "h.json")
    cfg = load_radar_config(Path("topic-radar.yml"))
    prior = _item(content="known", fetched_at=datetime(2026, 10, 12, 0, 0))
    pool.record_result(_source(), FetchResult(
        source_id="twitter_tier1", items=[prior], ok=True,
        started_at=prior.fetched_at, ended_at=prior.fetched_at,
    ))
    analyze(pool, cfg, history, "2026-10-12")
    early = _item(
        url="https://x.com/bea/status/2", canonical_url="https://x.com/bea/status/2",
        author="Bea Display", source_handle="@bea", content="BrandNewTerm x",
        engagement={"likes": 1}, fetched_at=datetime(2026, 10, 13, 0, 0),
    )
    late = _item(
        url=early.url, canonical_url=early.canonical_url, author="Other Name",
        source_handle="@bea", content="BrandNewTerm x", engagement={"likes": 180},
        fetched_at=datetime(2026, 10, 13, 4, 0),
    )
    for item in (early, late):
        pool.record_result(_source(), FetchResult(
            source_id="twitter_tier1", items=[item], ok=True,
            started_at=item.fetched_at, ended_at=item.fetched_at,
        ))
    report = analyze(pool, cfg, RadarHistory(history.path), "2026-10-13")
    lead = next(row for row in report["leads"] if row["term"] == "brandnewterm")
    assert lead["likes"] == 180
    assert lead["accounts"] == ["bea"]
    pool.close()
