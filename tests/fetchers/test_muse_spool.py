import json
from datetime import timedelta

import httpx
import pytest

from social_info._time import utcnow
from social_info.config import SourceConfig
from social_info.fetchers.muse_spool import MuseSpoolStale, fetch

URL = "https://www.threads.com/@george_sl_liu/post/DdzvTTIE3U_"


def source(path, **params):
    return SourceConfig(id="threads_muse", type="muse_spool", enabled=True, tier=1,
                        params={"path": str(path), **params})


def write_spool(path, collected_at, items):
    path.write_text(json.dumps({"collected_at": collected_at, "items": items}))


@pytest.mark.asyncio
async def test_fresh_spool_becomes_threads_items(tmp_path):
    spool = tmp_path / "radar.json"
    write_spool(spool, (utcnow() - timedelta(hours=2)).isoformat() + "+00:00", [
        {"url": URL, "text": "Claude Code 用 Opus 5.5 重跑 /insights", "ts": "2026-09-28T03:00:00+00:00"},
        {"url": "", "text": "no url"},
    ])
    async with httpx.AsyncClient() as client:
        items = await fetch(source(spool), client)
    assert [item.url for item in items] == [URL]
    item = items[0]
    assert item.source == "threads"
    assert item.source_handle == "@george_sl_liu"
    assert item.language == "zh"
    assert item.posted_at.tzinfo is None
    assert item.posted_at.isoformat() == "2026-09-28T03:00:00"


@pytest.mark.asyncio
async def test_stale_spool_raises_so_known_issues_sees_it(tmp_path):
    spool = tmp_path / "radar.json"
    write_spool(spool, (utcnow() - timedelta(hours=120)).isoformat(), [])
    async with httpx.AsyncClient() as client:
        with pytest.raises(MuseSpoolStale):
            await fetch(source(spool, max_age_hours=110), client)


@pytest.mark.asyncio
async def test_missing_spool_is_empty(tmp_path):
    async with httpx.AsyncClient() as client:
        assert await fetch(source(tmp_path / "absent.json"), client) == []
