"""Tests for read-only social_info.health CLI and cross-community resonance radar."""
import json
from datetime import datetime, timedelta
from pathlib import Path

from social_info._time import utcnow
from social_info.config import SourceConfig
from social_info.db import Database
from social_info.fetchers.base import FetchResult, Item
from social_info.health import compute_resonance, compute_source_health, main
from social_info.observation_pool import ObservationPool


def _seed_health_db(tmp_path: Path) -> tuple[Path, Path]:
    db_path = tmp_path / "state.db"
    cfg_path = tmp_path / "sources.yml"
    cfg_path.write_text(
        "sources:\n"
        "  - id: hn\n    type: hn_algolia\n    enabled: true\n"
        "  - id: silent_rss\n    type: rss\n    enabled: true\n"
        "  - id: stale_claw\n    type: clawhub\n    enabled: true\n"
        "  - id: broken_vb\n    type: rss\n    enabled: true\n"
        "  - id: retired_muse\n    type: muse_spool\n    enabled: false\n",
        encoding="utf-8",
    )
    now = utcnow()
    db = Database(db_path)
    db.init_schema()
    db.log_fetch_run("hn", now - timedelta(hours=2), now - timedelta(hours=2), "ok", 20, "", net_new=15)
    db.log_fetch_run("silent_rss", now - timedelta(hours=2), now - timedelta(hours=2), "ok", 0, "", net_new=0)
    db.log_fetch_run("stale_claw", now - timedelta(hours=2), now - timedelta(hours=2), "ok", 50, "", net_new=0)
    db.log_fetch_run("broken_vb", now - timedelta(hours=2), now - timedelta(hours=2), "failed", 0, "429", net_new=None)
    db.log_fetch_run("retired_muse", now - timedelta(hours=2), now - timedelta(hours=2), "failed", 0, "stale", net_new=None)
    db.close()
    return db_path, cfg_path


def test_health_opens_readonly_and_does_not_create_missing_db(tmp_path: Path, capsys):
    missing = tmp_path / "nonexistent.db"
    rc = main(["--db", str(missing)])
    assert rc == 2
    assert not missing.exists()
    captured = capsys.readouterr()
    assert "not found" in captured.err.lower()


def test_compute_source_health_tracks_empty_stale_and_filters_disabled(tmp_path: Path):
    db_path, cfg_path = _seed_health_db(tmp_path)
    rows = compute_source_health(db_path, days=7, config_path=cfg_path)
    by_src = {r["source"]: r for r in rows}

    assert "retired_muse" not in by_src
    assert by_src["hn"]["status_flag"] == "healthy"
    assert by_src["silent_rss"]["empty"] == 1
    assert by_src["silent_rss"]["status_flag"] == "empty"
    assert by_src["stale_claw"]["stale"] == 1
    assert by_src["stale_claw"]["status_flag"] == "all_stale"
    assert by_src["broken_vb"]["fail"] == 1
    assert by_src["broken_vb"]["status_flag"] == "failing"


def test_health_cli_json_and_problems_only(tmp_path: Path, capsys):
    db_path, cfg_path = _seed_health_db(tmp_path)
    rc = main(["--db", str(db_path), "--config", str(cfg_path), "--problems-only", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    sources = {r["source"] for r in payload["sources"]}
    assert sources == {"silent_rss", "stale_claw", "broken_vb"}


def test_compute_resonance_separates_organic_buzz_from_unmentioned_star_farm(tmp_path: Path):
    pool_path = tmp_path / "pool.db"
    pool = ObservationPool(pool_path)
    now = utcnow()
    day1 = now - timedelta(days=2)
    day2 = now - timedelta( hours=2)

    def _gh(slug: str, stars: int, ts: datetime) -> Item:
        url = f"https://github.com/{slug}"
        return Item(
            title=slug, url=url, canonical_url=url,
            source="github_trending", source_handle="trending:python:daily",
            source_tier=1, posted_at=ts, fetched_at=ts,
            excerpt="repo desc", engagement={"stars": stars},
        )

    pool.record_result(
        SourceConfig("github_trending", "github_trending", True, 1),
        FetchResult("github_trending", items=[_gh("real-org/real-agent", 1000, day1), _gh("farm-org/crack-suite", 400, day1)], started_at=day1, ended_at=day1),
    )
    pool.record_result(
        SourceConfig("github_trending", "github_trending", True, 1),
        FetchResult("github_trending", items=[_gh("real-org/real-agent", 1800, day2), _gh("farm-org/crack-suite", 1400, day2)], started_at=day2, ended_at=day2),
    )

    # Organic mentions across LinuxDO and Reddit for real-org/real-agent only
    ld_post = Item(
        title="實測 real-org/real-agent",
        url="https://linux.do/t/topic/100",
        canonical_url="https://linux.do/t/topic/100",
        source="linuxdo", source_handle="latest", source_tier=2,
        posted_at=day2, fetched_at=day2,
        excerpt="剛試了 https://github.com/real-org/real-agent 效果很穩",
    )
    rd_post = Item(
        title="real-org/real-agent vs alternatives",
        url="https://www.reddit.com/r/LocalLLaMA/comments/abc/x/",
        canonical_url="https://www.reddit.com/r/LocalLLaMA/comments/abc/x/",
        source="reddit", source_handle="r/LocalLLaMA", source_tier=1,
        posted_at=day2, fetched_at=day2,
        excerpt="Worth checking out real-org/real-agent",
    )
    pool.record_result(SourceConfig("linuxdo", "linuxdo", True, 2), FetchResult("linuxdo", items=[ld_post], started_at=day2, ended_at=day2))
    pool.record_result(SourceConfig("reddit_localllama", "reddit", True, 1), FetchResult("reddit_localllama", items=[rd_post], started_at=day2, ended_at=day2))
    pool.close()

    res = compute_resonance(pool_path, days=7)
    by_repo = {r["repo"]: r for r in res}
    assert by_repo["real-org/real-agent"]["star_delta"] == 800
    assert by_repo["real-org/real-agent"]["communities"] == ["linuxdo", "reddit"]
    assert by_repo["real-org/real-agent"]["signal"] == "resonant"

    assert by_repo["farm-org/crack-suite"]["star_delta"] == 1000
    assert by_repo["farm-org/crack-suite"]["community_mentions"] == 0
    assert by_repo["farm-org/crack-suite"]["signal"] == "unmentioned_spike"
