import asyncio
from datetime import datetime

from social_info.__main__ import _main
from social_info.config import SourceConfig
from social_info.db import Database
from social_info.fetchers.base import FetchResult, Item
from social_info.markdown import render_item
from social_info.observation_pool import ObservationPool


def test_analyze_pool_splices_raw_without_fetch(tmp_path, monkeypatch):
    db = Database(tmp_path / "state.db")
    db.init_schema()
    db.insert_item({
        "id": "1", "url": "https://example.com/old",
        "canonical_url": "https://example.com/old", "title": "Kept item",
        "title_hash": "h", "source": "hn", "source_handle": "front_page",
        "source_tier": 1, "posted_at": "2026-10-13T00:00:00",
        "fetched_at": "2026-10-13T00:00:00", "author": "",
        "excerpt": "old excerpt", "language": "en",
        "engagement_json": "{}", "also_appeared_in": "[]",
        "last_surfaced_at": "2026-10-13T01:00:00",
    })
    db.log_fetch_run(
        "hn", datetime(2026, 10, 13, 0, 0), datetime(2026, 10, 13, 0, 1),
        "ok", 1, "", net_new=0,
    )
    db.close()
    reports = tmp_path / "reports"
    reports.mkdir()
    raw = reports / "2026-10-14.md"
    raw.write_text(
        "# AI Daily Digest — 2026-10-14\n\n"
        "> stale (fetched ok but 0 net-new after dedup):\n"
        ">   - hn: fetched=1 net_new=0\n\n"
        "### 🔁 [Kept item](https://example.com/old)\n",
        encoding="utf-8",
    )
    pool = ObservationPool(tmp_path / "data" / "topic-pool.db")
    item = Item(
        title="HyperFrames", url="https://x.com/a/status/9",
        canonical_url="https://x.com/a/status/9", source="x",
        source_handle="@ada", source_tier=1,
        posted_at=datetime(2026, 10, 14, 1, 0),
        fetched_at=datetime(2026, 10, 14, 0, 0),
        author="ada", excerpt="short", content="Qwen3.8 Flash " + ("y" * 220),
        engagement={"likes": 200}, metadata={"content_kind": "post_text"},
    )
    pool.record_result(
        SourceConfig(id="twitter_tier1", type="twitter", enabled=True, tier=1),
        FetchResult(
            source_id="twitter_tier1", items=[item], ok=True,
            started_at=item.fetched_at, ended_at=item.fetched_at,
        ),
    )
    pool.close()
    monkeypatch.setattr(
        "sys.argv",
        ["social_info", "--analyze-pool", "--date", "2026-10-14",
         "--db", str(tmp_path / "state.db"), "--reports", str(reports),
         "--pool", str(tmp_path / "data" / "topic-pool.db"),
         "--radar-config", "topic-radar.yml"],
    )
    monkeypatch.setattr("social_info.__main__.load_dotenv", lambda: None)

    def fail(*args, **kwargs):
        raise AssertionError("analyze-pool must not fetch")

    monkeypatch.setattr("social_info.pipeline.run_pipeline", fail)
    asyncio.run(_main())
    text = raw.read_text(encoding="utf-8")
    assert "Kept item" in text
    assert "🔁" in text
    assert "stale" in text
    assert "https://x.com/a/status/9" in text
    assert "研究選題雷達" in text
    assert "baseline_only: true" in text
    assert "Baseline evidence" in text
    rendered = render_item(item)
    assert "來源提供的正文" in rendered
    assert "y" * 220 in rendered


def test_untyped_rss_content_is_not_promoted_to_feed_body(tmp_path):
    from social_info.pipeline import hydrate_item_content

    pool = ObservationPool(tmp_path / "pool.db")
    pool.conn.execute(
        "INSERT INTO observations ("
        "source_id, source_type, canonical_url, fetched_at, url, title, source, "
        "source_handle, posted_at, excerpt, content, content_status, metadata_json, partial"
        ") VALUES (?, ?, ?, ?, ?, ?, 'rss', ?, ?, ?, ?, 'content', '{}', 0)",
        (
            "ifanr_feed", "rss", "https://www.ifanr.com/a", "2026-10-13T00:00:00",
            "https://www.ifanr.com/a", "長文", "ifanr_feed", "2026-10-13T00:00:00",
            "短摘要", "y" * 240,
        ),
    )
    pool.conn.commit()
    item = Item(
        title="長文", url="https://www.ifanr.com/a", canonical_url="https://www.ifanr.com/a",
        source="rss", source_handle="ifanr_feed", source_tier=1,
        posted_at=datetime(2026, 10, 13, 0, 0), fetched_at=datetime(2026, 10, 13, 0, 0),
        excerpt="短摘要",
    )
    hydrate_item_content([item], pool, "2026-10-13")
    rendered = render_item(item)
    assert "來源提供的正文" not in rendered
    assert "y" * 240 not in rendered
    assert "短摘要" in rendered
    pool.close()


def test_splice_keeps_backslashes():
    from social_info.markdown import splice_radar_section

    raw = "before \\1 and \\q\n"
    out = splice_radar_section(raw, "radar \\1")
    assert "\\1" in out
    assert "\\q" in out
    again = splice_radar_section(out, "next \\q")
    assert "before \\1 and \\q" in again
    assert "next \\q" in again


def test_seed_cli_uses_source_config(tmp_path, monkeypatch):
    import json

    db = Database(tmp_path / "state.db")
    db.init_schema()
    db.insert_item({
        "id": "seed", "url": "https://x.com/ada/status/1",
        "canonical_url": "https://x.com/ada/status/1", "title": "Qwen3.8 Flash",
        "title_hash": "seed", "source": "x", "source_handle": "@ada",
        "source_tier": 1, "posted_at": "2026-10-14T00:00:00",
        "fetched_at": "2026-10-14T00:00:00", "author": "ada",
        "excerpt": "Qwen3.8 Flash", "language": "en",
        "engagement_json": '{"likes": 200}', "also_appeared_in": "[]",
        "last_surfaced_at": "2026-10-14T00:00:00",
    })
    db.close()
    config = tmp_path / "sources.yml"
    config.write_text(
        "sources:\n  - id: twitter_test\n    type: twitter\n"
        "    handles: [ada]\n    enabled: true\n    tier: 1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", [
        "social_info", "--analyze-pool", "--seed-pool", "--date", "2026-10-14",
        "--db", str(tmp_path / "state.db"), "--config", str(config),
        "--pool", str(tmp_path / "data/topic-pool.db"),
        "--reports", str(tmp_path / "reports"), "--radar-config", "topic-radar.yml",
    ])
    asyncio.run(_main())
    report = json.loads((tmp_path / "reports/topic-radar/2026-10-14.json").read_text())
    assert report["selected_sources"] == ["twitter_test"]
    assert report["status"] == "baseline_only"
    assert report["limited_input"] is True


def test_no_hash_column(tmp_path):
    pool = ObservationPool(tmp_path / "pool.db")
    names = {row["name"] for row in pool.conn.execute("PRAGMA table_info(observations)")}
    assert not any("hash" in name for name in names)
    pool.close()
