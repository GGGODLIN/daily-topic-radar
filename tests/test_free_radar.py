import json
from datetime import UTC, datetime

import pytest

from social_info.config import SourceConfig
from social_info.free_radar import collect, render_report, to_items
from social_info.vendor.new_term_radar import aisa, sitemaps


def _source(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"sitemaps": {}}))
    return SourceConfig("free_radar", "new_term_radar", True, 2, params={
        "config_path": str(config), "state_dir": str(tmp_path / "state"),
        "report_dir": str(tmp_path / "reports"), "pool_path": str(tmp_path / "pool.db"),
    })


def _transports(calls):
    def hf(cfg, state):
        calls.append("hf")
        return {"first_run": True, "terms": [], "raw": {
            "models": [{"id": "lab/Model", "pipeline_tag": "text-generation"}],
            "spaces": [{"id": "lab/Demo", "sdk": "gradio"}],
        }}

    def github(cfg, state):
        calls.append("github")
        return {"first_run": True, "repos": [{"repo": "lab/tool"}], "terms": [],
                "raw_repos": [{"full_name": "lab/tool", "stargazers_count": 400,
                               "description": "A local test tool"}]}

    def sitemap(cfg, state):
        calls.append("sitemap")
        return {"sites": {"test": {"first_run": True, "urls": 20, "new_urls": []}}, "terms": []}

    return {"hf_new": hf, "breakouts": github, "diff": sitemap}


def test_first_snapshot_shared_items_and_same_day_no_refetch(tmp_path):
    source = _source(tmp_path)
    calls = []
    now = datetime(2026, 10, 14, 12, tzinfo=UTC)
    report = collect(source, "2026-10-14", now=now, transports=_transports(calls))
    items = to_items(report)
    assert len(items) == 3
    assert "https://huggingface.co/spaces/lab/Demo" in {item.url for item in items}
    assert all(not item.metadata["proven_new"] for item in items)
    assert report["baseline"]["sitemap"]["test"]["new_urls"] == []
    assert "text-generation" in render_report(report)
    later = collect(source, "2026-10-14", now=datetime(2026, 10, 14, 13, tzinfo=UTC),
                    transports=_transports(calls))
    assert calls == ["hf", "github", "sitemap"]
    assert later["reused"] is True
    assert later["captured_at"] == report["captured_at"]
    assert later["raw_items"] == report["raw_items"]


def test_backdated_cached_report_preserves_vocab_and_post_timestamp(tmp_path):
    from pathlib import Path

    from social_info.fetchers.base import FetchResult, Item
    from social_info.observation_pool import ObservationPool
    from social_info.vendor.new_term_radar import snap

    source = _source(tmp_path)
    pool_path = Path(source.params["pool_path"])
    pool = ObservationPool(pool_path)
    posted = datetime(2026, 10, 13, 22)
    fetched = datetime(2026, 10, 14, 1)
    item = Item(
        title="HyperFrames", url="https://x.com/test/status/1", canonical_url="https://x.com/test/status/1",
        source="x", source_handle="@test", source_tier=1, posted_at=posted, fetched_at=fetched,
        content="HyperFrames launches", engagement={"likes": 200},
    )
    pool.record_result(SourceConfig("twitter", "twitter", True, 1), FetchResult(
        "twitter", items=[item], started_at=fetched, ended_at=fetched,
    ))
    pool.close()
    (pool_path.parent / "topic-radar.json").write_text(json.dumps({"terms": {"prior": "2026-10-13"}}))
    calls = []
    report = collect(source, "2026-10-14", now=datetime(2026, 10, 14, 12, tzinfo=UTC),
                     transports=_transports(calls))
    vocab_path = Path(source.params["state_dir"]) / "x_vocab.json.gz"
    before = vocab_path.read_bytes()
    later = collect(source, "2026-10-14", now=datetime(2026, 10, 15, 2, tzinfo=UTC),
                    transports=_transports(calls))
    assert later["reused"] is True
    assert calls == ["hf", "github", "sitemap"]
    assert vocab_path.read_bytes() == before
    assert set(snap.load(vocab_path).values()) <= {"2026-10-13", "2026-10-14"}
    x_items = [item for item in to_items(report) if item.source_handle == "x"]
    assert x_items and all(item.posted_at == posted for item in x_items)


def test_config_change_recollects_and_exposes_preserved_failure(tmp_path):
    source = _source(tmp_path)
    calls = []
    now = datetime(2026, 10, 14, 12, tzinfo=UTC)
    collect(source, "2026-10-14", now=now, transports=_transports(calls))
    config = {"sitemaps": {}, "breakout": {"min_stars": 350}}
    from pathlib import Path
    Path(source.params["config_path"]).write_text(json.dumps(config))
    transports = _transports(calls)

    def failed(cfg, state):
        raise RuntimeError("saved failure")

    transports["breakouts"] = failed
    report = collect(source, "2026-10-14", now=now, transports=transports)
    assert report["reused"] is False
    assert report["health"]["github"]["ok"] is False
    assert report["health"]["github"]["preserved"] is True
    assert any(row["title"] == "lab/tool" for row in report["raw_items"])


@pytest.mark.parametrize("call", [
    lambda: aisa.key(), lambda: aisa.call([]), lambda: aisa.call_all([]),
    lambda: aisa.chat("test", "test", "test"), lambda: aisa._post("https://example.com", {}, 1),
])
def test_paid_entry_points_block_before_network(monkeypatch, call):
    monkeypatch.setenv("AISA_API_KEY", "synthetic-test-value")

    def prohibited(*args, **kwargs):
        raise AssertionError("paid network access")

    monkeypatch.setattr("urllib.request.urlopen", prohibited)
    with pytest.raises(aisa.PaidCapabilityBlocked):
        call()


def test_sitemap_first_run_failure_and_next_snapshot(tmp_path, monkeypatch):
    cfg = {"sitemaps": {"test": {"index": "https://example.com/sitemap.xml", "pause": 0}}}
    monkeypatch.setattr(sitemaps.time, "sleep", lambda _: None)
    monkeypatch.setattr(sitemaps, "capture_site_urls", lambda *a: {"urls": ["https://example.com/tool-a"]})
    first = sitemaps.diff(cfg, tmp_path)
    assert first["sites"]["test"]["new_urls"] == []
    monkeypatch.setattr(sitemaps, "capture_site_urls", lambda *a: {"urls": [
        "https://example.com/tool-a", "https://example.com/tool-b",
    ]})
    second = sitemaps.diff(cfg, tmp_path)
    assert second["sites"]["test"]["new_urls"] == ["https://example.com/tool-b"]
    from social_info.vendor.new_term_radar import snap
    path = tmp_path / "sitemaps/test.json.gz"
    before = snap.load(path)

    def fail(*args):
        raise RuntimeError("source down")

    monkeypatch.setattr(sitemaps, "capture_site_urls", fail)
    failed = sitemaps.diff(cfg, tmp_path)
    assert "error" in failed["sites"]["test"]
    assert snap.load(path) == before
