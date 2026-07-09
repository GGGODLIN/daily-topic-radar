import tempfile
from datetime import datetime
from pathlib import Path

from social_info import pipeline
from social_info.config import Config, SourceConfig
from social_info.db import Database
from social_info.fetchers.base import FetchResult, Item
from social_info.pipeline import annotate_net_new, run_pipeline


def _item(url: str) -> Item:
    return Item(
        title=url,
        url=url,
        canonical_url=url,
        source="x",
        source_handle="@a",
        source_tier=1,
        posted_at=datetime(2026, 5, 18, 0, 0, 0),
        fetched_at=datetime(2026, 5, 18, 0, 0, 0),
    )


def test_annotate_net_new_counts_survivors_per_source_by_identity():
    i1, i2, i3 = _item("u1"), _item("u2"), _item("u3")
    a = FetchResult(source_id="x_tier1", items=[i1, i2], ok=True)
    b = FetchResult(source_id="hn_algolia", items=[i3], ok=True)
    # i2 was deduped away; only i1 (from a) and i3 (from b) survive
    new_items = [i1, i3]

    annotate_net_new([a, b], new_items)

    assert a.net_new == 1
    assert b.net_new == 1


def test_annotate_net_new_all_deduped_is_zero_not_none():
    i1, i2 = _item("u1"), _item("u2")
    stale = FetchResult(source_id="x_tier1", items=[i1, i2], ok=True)

    annotate_net_new([stale], new_items=[])

    assert stale.net_new == 0


def test_annotate_net_new_skips_failed_results():
    failed = FetchResult(source_id="x_tier1", items=[], ok=False, error="boom")

    annotate_net_new([failed], new_items=[])

    assert failed.net_new is None


async def test_pipeline_records_zero_net_new_for_all_deduped_source(monkeypatch):
    """Reproduces the X/Twitter bug: a source fetches ok but every item is a
    duplicate already in the DB -> 0 net-new -> previously invisible.
    Now the second run logs net_new=0 (distinct from a failed fetch)."""

    async def fake_fetch(source, http):
        return [_item("https://x.com/dup")]

    monkeypatch.setitem(pipeline.FETCHER_REGISTRY, "faketwitter", fake_fetch)
    config = Config(
        defaults={},
        sources=[SourceConfig(id="x_tier1", type="faketwitter",
                              enabled=True, tier=1)],
    )

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        db.init_schema()

        new1, res1 = await run_pipeline(config, db)
        assert len(new1) == 1
        assert res1[0].net_new == 1

        new2, res2 = await run_pipeline(config, db)
        assert new2 == []
        assert res2[0].ok is True
        assert res2[0].net_new == 0

        rows = [
            tuple(r) for r in db.conn.execute(
                "SELECT status, items_fetched, net_new FROM fetch_runs "
                "ORDER BY id"
            )
        ]
        db.close()

    assert rows == [("ok", 1, 1), ("ok", 1, 0)]
