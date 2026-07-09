import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from social_info import pipeline
from social_info._time import utcnow
from social_info.config import Config, SourceConfig
from social_info.db import Database
from social_info.dedup import Deduper, compute_item_id, compute_title_hash
from social_info.fetchers.base import Item
from social_info.pipeline import resolve_resurface_items, run_pipeline, write_report


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        d = Database(Path(tmp) / "test.db")
        d.init_schema()
        yield d
        d.close()


def _make_item(url, title):
    return Item(
        title=title, url=url, canonical_url=url,
        source="github_trending", source_handle="trending:python",
        source_tier=1,
        posted_at=datetime(2026, 7, 9, 0, 0, 0),
        fetched_at=datetime(2026, 7, 9, 0, 0, 0),
    )


def _insert_with_last_surfaced(db, item, last_surfaced_at):
    item_id = compute_item_id(item.canonical_url)
    row = item.to_db_row(item_id=item_id, title_hash=compute_title_hash(item.title))
    row["last_surfaced_at"] = last_surfaced_at
    db.conn.execute(
        "INSERT INTO items (id, url, canonical_url, title, title_hash, "
        "source, source_handle, source_tier, posted_at, fetched_at, "
        "language, engagement_json, also_appeared_in, last_surfaced_at) "
        "VALUES (:id, :url, :canonical_url, :title, :title_hash, :source, "
        ":source_handle, :source_tier, :posted_at, :fetched_at, :language, "
        ":engagement_json, :also_appeared_in, :last_surfaced_at)",
        row,
    )
    db.conn.commit()
    return item_id


def test_pipeline_resurface_updates_last_surfaced_at(db):
    item = _make_item("https://github.com/mattpocock/skills", "mattpocock/skills")
    old = (utcnow() - timedelta(days=40)).isoformat()
    item_id = _insert_with_last_surfaced(db, item, old)

    result = Deduper(db, resurface_days=30).process([item])
    assert item_id in result.resurface_ids

    db.update_last_surfaced_at(result.resurface_ids)

    updated = db.conn.execute(
        "SELECT last_surfaced_at FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    new_ts = datetime.fromisoformat(updated["last_surfaced_at"])
    assert (utcnow() - new_ts).total_seconds() < 5


def test_pipeline_resurface_skipped_when_within_n_days(db):
    item = _make_item("https://github.com/foo/bar", "foo/bar")
    recent = (utcnow() - timedelta(days=5)).isoformat()
    item_id = _insert_with_last_surfaced(db, item, recent)

    result = Deduper(db, resurface_days=30).process([item])
    assert result.resurface_ids == []

    before = db.conn.execute(
        "SELECT last_surfaced_at FROM items WHERE id = ?", (item_id,)
    ).fetchone()["last_surfaced_at"]
    assert before == recent


def test_resolve_resurface_items_rebuilds_full_item(db):
    item = _make_item("https://github.com/karpathy/skills", "karpathy/skills")
    old = (utcnow() - timedelta(days=35)).isoformat()
    _insert_with_last_surfaced(db, item, old)

    result = Deduper(db, resurface_days=30).process([item])
    resurfaced = resolve_resurface_items(db, result.resurface_ids)

    assert len(resurfaced) == 1
    assert resurfaced[0].url == "https://github.com/karpathy/skills"
    assert resurfaced[0].title == "karpathy/skills"
    assert resurfaced[0].source == "github_trending"


def test_write_report_marks_resurface_items_with_marker(db, tmp_path):
    item = _make_item("https://github.com/mattpocock/skills", "mattpocock/skills")
    old = (utcnow() - timedelta(days=35)).isoformat()
    _insert_with_last_surfaced(db, item, old)

    result = Deduper(db, resurface_days=30).process([item])
    resurfaced = resolve_resurface_items(db, result.resurface_ids)
    db.update_last_surfaced_at(result.resurface_ids)

    out_path = write_report(
        new_items=[],
        failures=[],
        out_dir=tmp_path,
        date="2026-07-09",
        generated_at=datetime(2026, 7, 9, 6, 0, 0),
        resurface_items=resurfaced,
    )

    content = out_path.read_text(encoding="utf-8")
    assert "🔁 [mattpocock/skills]" in content
    assert "total_items: 1" in content


def test_write_report_without_resurface_items_unmarked(db, tmp_path):
    item = _make_item("https://github.com/foo/bar", "foo/bar")

    out_path = write_report(
        new_items=[item],
        failures=[],
        out_dir=tmp_path,
        date="2026-07-09",
        generated_at=datetime(2026, 7, 9, 6, 0, 0),
    )

    content = out_path.read_text(encoding="utf-8")
    assert "🔁" not in content
    assert "[foo/bar]" in content


async def test_run_pipeline_returns_resurface_ids_for_end_to_end_wiring(db, monkeypatch):
    item = _make_item("https://github.com/mattpocock/skills", "mattpocock/skills")
    old = (utcnow() - timedelta(days=40)).isoformat()
    item_id = _insert_with_last_surfaced(db, item, old)

    async def fake_fetch(source, http):
        return [item]

    monkeypatch.setitem(pipeline.FETCHER_REGISTRY, "faketest_resurface", fake_fetch)
    config = Config(
        defaults={},
        sources=[SourceConfig(id="s1", type="faketest_resurface", enabled=True, tier=1)],
    )

    new_items, results, resurface_ids = await run_pipeline(config, db)

    assert resurface_ids == [item_id]
    assert new_items == []


async def test_main_wires_resurface_end_to_end(tmp_path, monkeypatch):
    import social_info.__main__ as main_mod

    db_path = tmp_path / "state.db"
    setup_db = Database(db_path)
    setup_db.init_schema()
    item = _make_item("https://github.com/mattpocock/skills", "mattpocock/skills")
    old = (utcnow() - timedelta(days=40)).isoformat()
    item_id = _insert_with_last_surfaced(setup_db, item, old)
    setup_db.close()

    async def fake_fetch(source, http):
        return [item]

    monkeypatch.setitem(pipeline.FETCHER_REGISTRY, "faketest_main_resurface", fake_fetch)

    sources_yml = tmp_path / "sources.yml"
    sources_yml.write_text(
        "sources:\n"
        "  - id: s1\n"
        "    type: faketest_main_resurface\n"
        "    enabled: true\n"
        "    tier: 1\n",
        encoding="utf-8",
    )
    reports_dir = tmp_path / "reports"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "social_info",
            "--config", str(sources_yml),
            "--db", str(db_path),
            "--reports", str(reports_dir),
            "--date", "2026-07-09",
        ],
    )

    rc = await main_mod._main()
    assert rc == 0

    out_path = reports_dir / "2026-07-09.md"
    content = out_path.read_text(encoding="utf-8")
    assert "🔁 [mattpocock/skills]" in content

    verify_db = Database(db_path)
    row = verify_db.conn.execute(
        "SELECT last_surfaced_at FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    verify_db.close()
    assert row["last_surfaced_at"] != old
    assert (utcnow() - datetime.fromisoformat(row["last_surfaced_at"])).total_seconds() < 5


async def test_main_dry_run_does_not_bump_last_surfaced_at(tmp_path, monkeypatch):
    import social_info.__main__ as main_mod

    db_path = tmp_path / "state.db"
    setup_db = Database(db_path)
    setup_db.init_schema()
    item = _make_item("https://github.com/mattpocock/skills", "mattpocock/skills")
    old = (utcnow() - timedelta(days=40)).isoformat()
    item_id = _insert_with_last_surfaced(setup_db, item, old)
    setup_db.close()

    async def fake_fetch(source, http):
        return [item]

    monkeypatch.setitem(pipeline.FETCHER_REGISTRY, "faketest_dryrun_resurface", fake_fetch)

    sources_yml = tmp_path / "sources.yml"
    sources_yml.write_text(
        "sources:\n"
        "  - id: s1\n"
        "    type: faketest_dryrun_resurface\n"
        "    enabled: true\n"
        "    tier: 1\n",
        encoding="utf-8",
    )
    reports_dir = tmp_path / "reports"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "social_info",
            "--config", str(sources_yml),
            "--db", str(db_path),
            "--reports", str(reports_dir),
            "--date", "2026-07-09",
            "--dry-run",
        ],
    )

    rc = await main_mod._main()
    assert rc == 0
    assert not reports_dir.exists()

    verify_db = Database(db_path)
    row = verify_db.conn.execute(
        "SELECT last_surfaced_at FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    verify_db.close()
    assert row["last_surfaced_at"] == old
