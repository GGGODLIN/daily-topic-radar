from datetime import datetime, timedelta
from pathlib import Path

import pytest

from social_info.config import Config, SourceConfig
from social_info.db import Database
from social_info.fetchers.linuxdo import (
    FETCH_BUDGET_SECONDS,
    LinuxDoFetchError,
    _strip_html,
    fetch,
)
from social_info.pipeline import run_pipeline, write_report

NOW = datetime(2026, 10, 11, 10, 30, 0)
PAGE = Path("tests/fixtures/linuxdo_latest_page.xml")
OVERLAP = Path("tests/fixtures/linuxdo_latest_page_overlap.xml")
RICH = (
    "<p>First paragraph.</p><p>Second paragraph.</p>"
    "<p><small>2 个帖子 - 2 位参与者</small></p><p>阅读完整话题</p>"
)
CURL_SCRIPT = r"""#!/bin/sh
page=0
for arg in "$@"; do
  case "$arg" in
    *\?page=*|*\&page=*) page=${arg#*page=}; page=${page%%[!0-9]*} ;;
  esac
done
printf '%s\n' "$page" >> "$CURL_LOG"
if [ "$CURL_MODE" = "timeout" ]; then
  sleep "$CURL_SLEEP"
fi
if [ "$CURL_MODE" = "page1-429" ] && [ "$page" != "0" ]; then
  printf '\n__SOCIAL_INFO_HTTP__429'
  exit 0
fi
if [ "$CURL_MODE" = "rc" ]; then
  echo "curl: transport failed" >&2
  exit 28
fi
if [ ! -f "$CURL_BODY_DIR/page${page}.xml" ]; then
  echo "missing page $page" >&2
  exit 1
fi
cat "$CURL_BODY_DIR/page${page}.xml"
printf '\n__SOCIAL_INFO_HTTP__%s' "$CURL_STATUS"
"""
_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MONTHS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def _rfc(moment):
    return (
        f"{_DAYS[moment.weekday()]}, {moment.day:02d} "
        f"{_MONTHS[moment.month - 1]} {moment.year} "
        f"{moment:%H:%M:%S} +0000"
    )


def _cfg(**params):
    merged = {"window_hours": 24, "max_pages": 5}
    merged.update(params)
    return SourceConfig(
        id="linuxdo",
        type="linuxdo",
        enabled=True,
        tier=2,
        language="zh-CN",
        params=merged,
    )


def _clock():
    def current():
        return NOW
    return current


async def _no_sleep(_seconds):
    return None


def _install_curl(tmp_path, monkeypatch, *, status="200", mode="ok", sleep_s="8"):
    bin_dir = tmp_path / "bin"
    body_dir = tmp_path / "bodies"
    bin_dir.mkdir()
    body_dir.mkdir()
    log = tmp_path / "curl.log"
    script = bin_dir / "curl"
    script.write_text(CURL_SCRIPT, encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("CURL_LOG", str(log))
    monkeypatch.setenv("CURL_BODY_DIR", str(body_dir))
    monkeypatch.setenv("CURL_STATUS", status)
    monkeypatch.setenv("CURL_MODE", mode)
    monkeypatch.setenv("CURL_SLEEP", sleep_s)
    return body_dir, log


def _prepare(monkeypatch, tmp_path, **curl):
    monkeypatch.setattr("social_info.fetchers.linuxdo.utcnow", _clock())
    monkeypatch.setattr("social_info.fetchers.linuxdo.asyncio.sleep", _no_sleep)
    return _install_curl(tmp_path, monkeypatch, **curl)


def _put(body_dir, page, text):
    (body_dir / f"page{page}.xml").write_text(text, encoding="utf-8")


def _calls(log):
    return log.read_text(encoding="utf-8").splitlines()


def _entry(index, moment, **overrides):
    entry = {
        "title": f"Topic {index}",
        "link": f"https://linux.do/t/topic/{3000 + index}",
        "pubDate": _rfc(moment),
        "description": "<p>body</p>",
        "author": "alice",
        "category": "开发调优",
    }
    entry.update(overrides)
    return entry


def _batch(count, newest, *, start=0):
    return [
        _entry(start + index, newest - timedelta(minutes=index))
        for index in range(count)
    ]


def _feed(entries):
    items = []
    for entry in entries:
        items.append(
            "<item>"
            f"<title>{entry['title']}</title>"
            f"<dc:creator><![CDATA[{entry['author']}]]></dc:creator>"
            f"<category>{entry['category']}</category>"
            f"<description><![CDATA[{entry['description']}]]></description>"
            f"<link>{entry['link']}</link>"
            f"<pubDate>{entry['pubDate']}</pubDate>"
            "</item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" ?>'
        '<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<channel><title>LINUX DO - 最新话题</title>"
        + "".join(items)
        + "</channel></rss>\n"
    )


def _window(body_dir):
    newest = datetime(2026, 10, 11, 10, 10, 0)
    entries = _batch(100, newest)
    entries[0]["title"] = "Newer topic one"
    entries[0]["description"] = RICH
    entries[-1]["link"] = "https://linux.do/t/topic/1002"
    _put(body_dir, 0, _feed(entries))
    _put(body_dir, 1, OVERLAP.read_text(encoding="utf-8"))


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "state.db")
    database.init_schema()
    yield database
    database.close()


def test_budget_fits_the_existing_runner():
    # per_page=100. Worst case remains 50 * 25s + 49 * 30s = 2720s.
    # Source budget covers that; runner hard limit is seq 1 110 (3300s).
    assert FETCH_BUDGET_SECONDS == 3000
    assert FETCH_BUDGET_SECONDS < 3300


def test_strip_html_drops_topic_footer():
    raw = "<p>Hello.</p><p><small>3 个帖子 - 1 位参与者</small></p><p>阅读完整话题</p>"
    assert _strip_html(raw) == "Hello."


@pytest.mark.asyncio
async def test_fetch_stops_only_after_the_window_and_dedups(monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path)
    _window(body_dir)

    items = await fetch(_cfg())

    assert _calls(log) == ["0", "1"]
    assert len(items) == 100
    assert [item.url for item in items].count("https://linux.do/t/topic/1002") == 1
    assert "https://linux.do/t/topic/900" not in [item.url for item in items]
    first = items[0]
    assert first.title == "Newer topic one"
    assert first.source == "linuxdo"
    assert first.source_handle == "开发调优"
    assert first.author == "alice"
    assert first.posted_at == datetime(2026, 10, 11, 10, 10, 0)
    assert "First paragraph." in first.excerpt
    assert "阅读完整话题" not in first.excerpt


@pytest.mark.asyncio
async def test_fetch_keeps_every_raw_entry(monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path)
    entries = _batch(100, datetime(2026, 10, 11, 10, 10, 0), start=5000)
    _put(body_dir, 0, _feed(entries))
    _put(body_dir, 1, _feed([
        _entry(1, datetime(2026, 10, 9, 1, 0, 0), title="Old topic"),
    ]))

    items = await fetch(_cfg())

    assert _calls(log) == ["0", "1"]
    assert len(items) == 100
    assert items[-1].url == "https://linux.do/t/topic/8099"


@pytest.mark.asyncio
async def test_old_page_ends_the_window(monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path)
    _put(body_dir, 0, _feed(_batch(2, datetime(2026, 10, 9, 10, 0, 0))))

    assert await fetch(_cfg()) == []
    assert _calls(log) == ["0"]


@pytest.mark.asyncio
async def test_short_page_keeps_valid_items_and_reports_incomplete(db, monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path)
    _put(body_dir, 0, PAGE.read_text(encoding="utf-8"))
    config = Config(defaults={}, sources=[_cfg()])

    new_items, results, _ids = await run_pipeline(config, db)

    assert [item.url for item in new_items] == [
        "https://linux.do/t/topic/1001",
        "https://linux.do/t/topic/1002",
    ]
    assert results[0].ok is False
    assert "not treating it as the end" in results[0].error
    assert results[0].net_new == 2
    assert db.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
    assert _calls(log) == ["0"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("body", "match"), [
    (
        '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>',
        "no entries|not valid",
    ),
    ("<rss><channel><<<>>></channel></rss>", "not valid|no title|no entries"),
    ("<html>Just a moment</html>", "not an RSS"),
])
async def test_malformed_feed_fails(monkeypatch, tmp_path, body, match):
    body_dir, log = _prepare(monkeypatch, tmp_path)
    _put(body_dir, 0, body)

    with pytest.raises(LinuxDoFetchError, match=match):
        await fetch(_cfg())
    assert _calls(log) == ["0"]


@pytest.mark.asyncio
async def test_missing_date_fails(monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path)
    _put(body_dir, 0, (
        '<?xml version="1.0"?><rss version="2.0"><channel><item>'
        "<title>No date</title><link>https://linux.do/t/topic/1</link>"
        "</item></channel></rss>"
    ))

    with pytest.raises(LinuxDoFetchError, match="no pubDate"):
        await fetch(_cfg())
    assert _calls(log) == ["0"]


@pytest.mark.asyncio
async def test_invalid_item_fails(monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path)
    _put(body_dir, 0, _feed([
        _entry(1, datetime(2026, 10, 11, 10, 0, 0), title=" "),
    ]))

    with pytest.raises(LinuxDoFetchError, match="no title"):
        await fetch(_cfg())
    assert _calls(log) == ["0"]


@pytest.mark.asyncio
async def test_unordered_page_fails(monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path)
    _put(body_dir, 0, _feed([
        _entry(1, datetime(2026, 10, 11, 9, 0, 0)),
        _entry(2, datetime(2026, 10, 11, 10, 0, 0)),
    ]))

    with pytest.raises(LinuxDoFetchError, match="newest-first"):
        await fetch(_cfg())
    assert _calls(log) == ["0"]


@pytest.mark.asyncio
async def test_page_that_does_not_advance_fails(monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path)
    _put(body_dir, 0, _feed(_batch(100, datetime(2026, 10, 11, 10, 10, 0))))
    _put(body_dir, 1, _feed(_batch(100, datetime(2026, 10, 11, 12, 0, 0), start=9000)))

    with pytest.raises(LinuxDoFetchError, match="did not advance"):
        await fetch(_cfg())
    assert _calls(log) == ["0", "1"]


@pytest.mark.asyncio
async def test_duplicate_page_fails(monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path)
    entries = _batch(100, datetime(2026, 10, 11, 10, 10, 0))
    _put(body_dir, 0, _feed(entries))
    _put(body_dir, 1, _feed([entries[-1]]))

    with pytest.raises(LinuxDoFetchError, match="repeated the previous page"):
        await fetch(_cfg())
    assert _calls(log) == ["0", "1"]


@pytest.mark.asyncio
async def test_duplicate_entry_on_one_page_fails(monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path)
    first = _entry(1, datetime(2026, 10, 11, 10, 10, 0))
    second = _entry(1, datetime(2026, 10, 11, 10, 0, 0))
    _put(body_dir, 0, _feed([first, second]))

    with pytest.raises(LinuxDoFetchError, match="repeats an entry"):
        await fetch(_cfg())
    assert _calls(log) == ["0"]


@pytest.mark.asyncio
async def test_page_cap_fails_instead_of_truncating(monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path)
    _put(body_dir, 0, _feed(_batch(100, datetime(2026, 10, 11, 10, 10, 0))))

    with pytest.raises(LinuxDoFetchError, match="refusing to truncate"):
        await fetch(_cfg(max_pages=1))
    assert _calls(log) == ["0"]


@pytest.mark.asyncio
async def test_time_budget_fails_instead_of_continuing(monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path)
    monkeypatch.setattr("social_info.fetchers.linuxdo.FETCH_BUDGET_SECONDS", 0)
    _put(body_dir, 0, _feed(_batch(30, datetime(2026, 10, 11, 10, 10, 0))))

    with pytest.raises(LinuxDoFetchError, match="exhausted"):
        await fetch(_cfg())
    assert not log.exists()


@pytest.mark.asyncio
async def test_time_budget_counts_the_next_request(monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path)
    monkeypatch.setattr("social_info.fetchers.linuxdo.FETCH_BUDGET_SECONDS", 26)
    _put(body_dir, 0, _feed(_batch(100, datetime(2026, 10, 11, 10, 10, 0))))
    _put(body_dir, 1, _feed(_batch(100, datetime(2026, 10, 10, 9, 0, 0), start=9000)))

    with pytest.raises(LinuxDoFetchError, match="exhausted"):
        await fetch(_cfg())
    assert _calls(log) == ["0"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "match"), [("429", "429"), ("500", "HTTP 500")])
async def test_http_error_fails(monkeypatch, tmp_path, status, match):
    body_dir, log = _prepare(monkeypatch, tmp_path, status=status)
    _put(body_dir, 0, "<html>no</html>")

    with pytest.raises(LinuxDoFetchError, match=match):
        await fetch(_cfg())
    assert _calls(log) == ["0"]


@pytest.mark.asyncio
async def test_curl_failure_fails(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path, mode="rc")

    with pytest.raises(LinuxDoFetchError, match="curl failed"):
        await fetch(_cfg())


@pytest.mark.asyncio
async def test_curl_timeout_fails(monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path, mode="timeout", sleep_s="8")
    monkeypatch.setattr("social_info.fetchers.linuxdo.REQUEST_TIMEOUT_SECONDS", 1)

    with pytest.raises(LinuxDoFetchError, match="timed out"):
        await fetch(_cfg())
    assert _calls(log) == ["0"]


@pytest.mark.asyncio
async def test_pipeline_second_run_inserts_nothing(db, monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path)
    _window(body_dir)
    config = Config(defaults={}, sources=[_cfg()])
    reports = tmp_path / "reports"
    reports.mkdir()

    first, _, _ = await run_pipeline(config, db)
    report = write_report(
        first,
        [],
        out_dir=reports,
        date="2026-10-11",
        generated_at=NOW,
    )
    second, _, _ = await run_pipeline(config, db)

    assert len(first) == 100
    assert second == []
    assert _calls(log) == ["0", "1", "0", "1"]
    stored = db.conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
    assert stored == 100
    text = report.read_text(encoding="utf-8")
    assert "LINUX DO" in text
    assert "Newer topic one" in text
    rows = db.conn.execute(
        "SELECT status, net_new FROM fetch_runs ORDER BY id"
    ).fetchall()
    assert [row["status"] for row in rows] == ["ok", "ok"]
    assert rows[1]["net_new"] == 0


@pytest.mark.asyncio
async def test_pipeline_rate_limit_is_a_reported_failure(db, monkeypatch, tmp_path):
    body_dir, _log = _prepare(monkeypatch, tmp_path, status="429")
    _put(body_dir, 0, "<html>no</html>")
    config = Config(defaults={}, sources=[_cfg()])
    reports = tmp_path / "reports"
    reports.mkdir()

    new_items, results, _ids = await run_pipeline(config, db)
    failed = [result for result in results if not result.ok]
    report = write_report(
        new_items,
        failed,
        out_dir=reports,
        date="2026-10-11",
        generated_at=NOW,
    )

    assert new_items == []
    assert failed[0].error_class == "persistent_error"
    assert "429" in failed[0].error
    stored = db.conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
    assert stored == 0
    row = db.conn.execute(
        "SELECT status, error FROM fetch_runs"
    ).fetchone()
    assert row["status"] == "failed"
    assert "429" in row["error"]
    text = report.read_text(encoding="utf-8")
    assert "sources_failed: 1" in text
    assert "linuxdo:" in text


def _assert_kept_failure(new_items, results, expected_urls):
    failed = [result for result in results if not result.ok]
    assert len(failed) == 1
    assert failed[0].ok is False
    assert [item.url for item in new_items] == expected_urls
    assert [item.url for item in failed[0].items] == expected_urls
    return failed[0]


@pytest.mark.asyncio
async def test_pipeline_keeps_page0_when_page1_is_rate_limited(db, monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path, mode="page1-429")
    kept = _batch(100, datetime(2026, 10, 11, 10, 10, 0))
    kept[0]["title"] = "Kept topic"
    _put(body_dir, 0, _feed(kept))
    config = Config(defaults={}, sources=[_cfg()])
    reports = tmp_path / "reports"
    reports.mkdir()

    new_items, results, _ids = await run_pipeline(config, db)
    failed = _assert_kept_failure(
        new_items, results, [item["link"] for item in kept]
    )
    report = write_report(
        new_items, [failed], out_dir=reports, date="2026-10-11", generated_at=NOW,
    )

    assert _calls(log) == ["0", "1"]
    assert "429" in failed.error
    stored = [
        row["title"]
        for row in db.conn.execute("SELECT title FROM items ORDER BY posted_at DESC")
    ]
    assert stored[0] == "Kept topic"
    text = report.read_text(encoding="utf-8")
    assert "sources_failed: 1" in text
    assert "429" in text
    assert "## LINUX DO (100 items)" in text
    assert "Kept topic" in text
    row = db.conn.execute("SELECT status, net_new FROM fetch_runs").fetchone()
    assert row["status"] == "failed"
    assert row["net_new"] == 100


@pytest.mark.asyncio
async def test_failed_page_items_do_not_join_the_kept_set(db, monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path)
    kept = _batch(100, datetime(2026, 10, 11, 10, 10, 0))
    _put(body_dir, 0, _feed(kept))
    rejected = _batch(100, datetime(2026, 10, 11, 8, 0, 0), start=9000)
    rejected[0], rejected[1] = rejected[1], rejected[0]
    rejected[0]["title"] = "Unordered rejected"
    rejected[1]["title"] = "Must not leak"
    _put(body_dir, 1, _feed(rejected))
    config = Config(defaults={}, sources=[_cfg()])

    new_items, results, _ids = await run_pipeline(config, db)
    failed = _assert_kept_failure(
        new_items, results, [item["link"] for item in kept]
    )

    assert "newest-first" in failed.error
    assert _calls(log) == ["0", "1"]
    stored = [
        row["title"]
        for row in db.conn.execute("SELECT title FROM items")
    ]
    assert "Unordered rejected" not in stored
    assert "Must not leak" not in stored


@pytest.mark.asyncio
async def test_pipeline_page0_failure_keeps_nothing(db, monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path, status="429")
    _put(body_dir, 0, _feed(_batch(100, datetime(2026, 10, 11, 10, 10, 0))))
    config = Config(defaults={}, sources=[_cfg()])

    new_items, results, _ids = await run_pipeline(config, db)

    assert new_items == []
    assert results[0].items == []
    assert _calls(log) == ["0"]
    stored = db.conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
    assert stored == 0


@pytest.mark.asyncio
async def test_pipeline_partial_replay_inserts_nothing(db, monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path, mode="page1-429")
    kept = _batch(100, datetime(2026, 10, 11, 10, 10, 0))
    _put(body_dir, 0, _feed(kept))
    config = Config(defaults={}, sources=[_cfg()])

    first, _, _ = await run_pipeline(config, db)
    second, second_results, _ids = await run_pipeline(config, db)

    assert len(first) == 100
    assert second == []
    assert second_results[0].ok is False
    assert second_results[0].net_new == 0
    assert _calls(log) == ["0", "1", "0", "1"]
    stored = db.conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
    assert stored == 100


@pytest.mark.asyncio
async def test_pipeline_keeps_items_when_the_page_cap_stops_the_window(
    db, monkeypatch, tmp_path
):
    body_dir, log = _prepare(monkeypatch, tmp_path)
    kept = _batch(100, datetime(2026, 10, 11, 10, 10, 0))
    _put(body_dir, 0, _feed(kept))
    _put(body_dir, 1, _feed(_batch(100, datetime(2026, 10, 11, 8, 0, 0), start=9000)))
    config = Config(defaults={}, sources=[_cfg(max_pages=1)])

    new_items, results, _ids = await run_pipeline(config, db)
    failed = _assert_kept_failure(
        new_items, results, [item["link"] for item in kept]
    )

    assert "refusing to truncate" in failed.error
    assert _calls(log) == ["0"]
    stored = db.conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
    assert stored == 100


@pytest.mark.asyncio
async def test_pipeline_keeps_items_when_the_budget_stops_the_window(
    db, monkeypatch, tmp_path
):
    body_dir, log = _prepare(monkeypatch, tmp_path)
    monkeypatch.setattr("social_info.fetchers.linuxdo.FETCH_BUDGET_SECONDS", 26)
    kept = _batch(100, datetime(2026, 10, 11, 10, 10, 0))
    _put(body_dir, 0, _feed(kept))
    _put(body_dir, 1, _feed(_batch(100, datetime(2026, 10, 10, 9, 0, 0), start=9000)))
    config = Config(defaults={}, sources=[_cfg()])

    new_items, results, _ids = await run_pipeline(config, db)
    failed = _assert_kept_failure(
        new_items, results, [item["link"] for item in kept]
    )

    assert "exhausted" in failed.error
    assert _calls(log) == ["0"]
    stored = db.conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
    assert stored == 100


@pytest.mark.asyncio
async def test_pipeline_complete_window_stays_successful(db, monkeypatch, tmp_path):
    body_dir, log = _prepare(monkeypatch, tmp_path)
    _window(body_dir)
    config = Config(defaults={}, sources=[_cfg()])

    new_items, results, _ids = await run_pipeline(config, db)

    assert len(new_items) == 100
    assert results[0].ok is True
    assert results[0].error == ""
    assert _calls(log) == ["0", "1"]
    row = db.conn.execute("SELECT status FROM fetch_runs").fetchone()
    assert row["status"] == "ok"
