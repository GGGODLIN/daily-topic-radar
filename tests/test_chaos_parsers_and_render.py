"""Chaos and edge-case tests exposing parser, timezone, redaction, and markdown bugs.

All network and subprocess calls are mocked offline. Synthetic credentials are
assembled at runtime so no real-looking secret literal is stored in the repo.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from social_info import free_radar
from social_info.config import SourceConfig
from social_info.fetchers import (
    clawhub,
    devpost,
    github_search,
    github_trending,
    hn,
    huggingface,
    linuxdo,
    muse_spool,
    product_hunt,
    reddit,
    rss,
    rsshub,
    skillhub_cn,
    skills_sh,
    threads,
    threads_apify,
    trendshift,
    v2ex,
    wewe_rss,
)
from social_info.fetchers.base import Item
from social_info.markdown import RADAR_END, RADAR_START, render_item, splice_radar_section
from social_info.redact import redact_item
from social_info.url_utils import canonical_url


def _src(source_type: str, source_id: str = "test_src", **params) -> SourceConfig:
    return SourceConfig(
        id=source_id,
        type=source_type,
        enabled=True,
        tier=1,
        language="en",
        params=params,
    )


def _fake_sk() -> str:
    return "sk-" + "proj-" + "A" * 28


def _fake_gh() -> str:
    return "ghp_" + "a" * 36


# ---------------------------------------------------------------------------
# 1. url_utils.canonical_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://example.com[1]/post",
        "https://[::1/bad",
        "https://example.com:99999/a",
        None,
    ],
)
def test_canonical_url_malformed_does_not_raise(bad_url):
    result = canonical_url(bad_url)  # type: ignore[arg-type]
    assert isinstance(result, str)


def test_canonical_url_normalization_consistency():
    assert canonical_url("https://example.com") == canonical_url("https://example.com/")
    assert canonical_url("https://example.com:443/a") == canonical_url("https://example.com/a")
    assert canonical_url("http://example.com:80/a") == canonical_url("http://example.com/a")
    assert (
        canonical_url("https://example.com/a?UTM_SOURCE=x&Ref=hn&FBCLID=1&id=2")
        == "https://example.com/a?id=2"
    )


# ---------------------------------------------------------------------------
# 2. fetchers/trendshift.py
# ---------------------------------------------------------------------------


def test_trendshift_decode_streaming_payload_preserves_cjk_and_emoji():
    chunk_inner = r'{\"full_name\":\"org/repo\",\"rank\":1,\"score\":10,\"description\":\"中文 🚀\"}'
    html = f'<script>self.__next_f.push([1,"{chunk_inner}"])</script>'
    decoded = trendshift._decode_streaming_payload(html)
    assert "中文 🚀" in decoded


@pytest.mark.parametrize(
    "bad_inner",
    [
        "trailing-slash\\",
        r"truncated-\u12",
    ],
)
def test_trendshift_decode_streaming_payload_malformed_escape_no_crash(bad_inner):
    html = f'<script>self.__next_f.push([1,"{bad_inner}"])</script>'
    decoded = trendshift._decode_streaming_payload(html)
    assert isinstance(decoded, str)


@pytest.mark.asyncio
async def test_trendshift_fetch_handles_null_and_bad_rank_score(httpx_mock):
    payload_objs = [
        {"full_name": "org/good-null-desc", "rank": 1, "score": 42, "description": None},
        {"full_name": "org/bad-rank", "rank": None, "score": 10, "description": "bad"},
        {"full_name": "org/bad-score", "rank": 2, "score": "N/A", "description": "ok"},
    ]
    inner = json.dumps(payload_objs, separators=(",", ":")).replace("\\", "\\\\").replace('"', '\\"')
    html = f'<script>self.__next_f.push([1,"{inner}"])</script>'
    httpx_mock.add_response(url=re.compile(r"https://trendshift\.io.*"), text=html)

    cfg = _src("trendshift", "trendshift", limit=25)
    async with httpx.AsyncClient() as client:
        items = await trendshift.fetch(cfg, client)

    by_title = {it.title: it for it in items}
    assert "org/good-null-desc" in by_title
    assert by_title["org/good-null-desc"].excerpt == ""
    assert "org/bad-rank" not in by_title
    if "org/bad-score" in by_title:
        assert by_title["org/bad-score"].engagement["score"] == 0


# ---------------------------------------------------------------------------
# 3. fetchers/linuxdo.py
# ---------------------------------------------------------------------------


def test_linuxdo_strip_html_literal_pre_token_does_not_crash():
    out = linuxdo._strip_html("<p>text \x00PRE999\x00 end</p>")
    assert "text" in out
    assert "end" in out


def test_linuxdo_fetch_page_text_invalid_utf8_no_raw_unicode_decode_error(monkeypatch):
    def fake_run(*args, **kwargs):
        # Simulate subprocess.run(..., text=True) / binary decode error or invalid UTF-8 bytes
        if kwargs.get("text"):
            raise UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "invalid start byte")
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=b"<rss><channel></channel></rss>\xff\xfe\n__SOCIAL_INFO_HTTP__200",
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    try:
        text = linuxdo._fetch_page_text(0)
        assert isinstance(text, str)
    except linuxdo.LinuxDoFetchError:
        pass


@pytest.mark.parametrize("year", [0, 10000])
def test_linuxdo_entry_posted_at_extreme_year_raises_fetch_error(year):
    fake_struct = time.struct_time((year, 1, 1, 0, 0, 0, 0, 1, 0))
    entry = {"published_parsed": fake_struct}
    with pytest.raises(linuxdo.LinuxDoFetchError):
        linuxdo._entry_posted_at(entry, page=0, index=0)


# ---------------------------------------------------------------------------
# 4. Timestamps, timezone normalization & malformed API payloads
# ---------------------------------------------------------------------------


def _rss_xml(pub_date: str, *, include_title: bool = True, include_link: bool = True) -> str:
    title_el = "<title>Sample Entry</title>" if include_title else ""
    link_el = "<link>https://example.com/item-1</link>" if include_link else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<rss version=\"2.0\"><channel><title>Feed</title>"
        f"<item>{title_el}{link_el}<pubDate>{pub_date}</pubDate></item>"
        "</channel></rss>"
    )


@pytest.mark.asyncio
async def test_rss_extreme_year_returns_fallback(httpx_mock):
    httpx_mock.add_response(
        url="https://feed.example.test/extreme.xml",
        text=_rss_xml("Mon, 01 Jan 0001 00:00:00 GMT"),
    )
    async with httpx.AsyncClient() as client:
        items_ext = await rss.fetch(
            _src("rss", "rss_ext", url="https://feed.example.test/extreme.xml"),
            client,
        )
    assert len(items_ext) == 1


@pytest.mark.asyncio
async def test_rss_gmt_not_shifted_by_local_tz(httpx_mock, monkeypatch):
    httpx_mock.add_response(
        url="https://feed.example.test/gmt.xml",
        text=_rss_xml("Sun, 08 Mar 2026 02:30:00 GMT"),
    )
    monkeypatch.setenv("TZ", "America/New_York")
    if hasattr(time, "tzset"):
        time.tzset()
    try:
        async with httpx.AsyncClient() as client:
            items_gmt = await rss.fetch(
                _src("rss", "rss_gmt", url="https://feed.example.test/gmt.xml"),
                client,
            )
        assert items_gmt[0].posted_at == datetime(2026, 3, 8, 2, 30, 0)
    finally:
        monkeypatch.delenv("TZ", raising=False)
        if hasattr(time, "tzset"):
            time.tzset()


@pytest.mark.asyncio
async def test_rss_none_title_link_and_year_10000(httpx_mock):
    fake_parsed = type(
        "Parsed",
        (),
        {
            "entries": [
                {"title": None, "link": None},
                {
                    "title": "Valid",
                    "link": "https://example.com/v",
                    "published_parsed": time.struct_time((10000, 1, 1, 0, 0, 0, 0, 1, 0)),
                },
            ]
        },
    )()
    httpx_mock.add_response(url="https://feed.example.test/none.xml", text="<rss/>")
    with patch("social_info.fetchers.rss.feedparser.parse", return_value=fake_parsed):
        async with httpx.AsyncClient() as client:
            items_none = await rss.fetch(
                _src("rss", "rss_none", url="https://feed.example.test/none.xml"),
                client,
            )
    assert len(items_none) == 1
    assert items_none[0].title == "Valid"


@pytest.mark.asyncio
async def test_rsshub_extreme_year_returns_fallback(httpx_mock, monkeypatch):
    monkeypatch.setenv("RSSHUB_INSTANCE_URL", "https://rsshub.example.test")
    httpx_mock.add_response(
        url="https://rsshub.example.test/extreme",
        text=_rss_xml("Mon, 01 Jan 0001 00:00:00 GMT"),
    )
    async with httpx.AsyncClient() as client:
        items_ext = await rsshub.fetch(_src("rsshub", "rh_ext", path="/extreme"), client)
    assert len(items_ext) == 1


@pytest.mark.asyncio
async def test_rsshub_gmt_not_shifted_by_local_tz(httpx_mock, monkeypatch):
    monkeypatch.setenv("RSSHUB_INSTANCE_URL", "https://rsshub.example.test")
    httpx_mock.add_response(
        url="https://rsshub.example.test/gmt",
        text=_rss_xml("Sun, 08 Mar 2026 02:30:00 GMT"),
    )
    monkeypatch.setenv("TZ", "America/New_York")
    if hasattr(time, "tzset"):
        time.tzset()
    try:
        async with httpx.AsyncClient() as client:
            items_gmt = await rsshub.fetch(_src("rsshub", "rh_gmt", path="/gmt"), client)
        assert items_gmt[0].posted_at == datetime(2026, 3, 8, 2, 30, 0)
    finally:
        monkeypatch.delenv("TZ", raising=False)
        if hasattr(time, "tzset"):
            time.tzset()


@pytest.mark.asyncio
async def test_rsshub_none_title_link_and_year_10000(httpx_mock, monkeypatch):
    monkeypatch.setenv("RSSHUB_INSTANCE_URL", "https://rsshub.example.test")
    fake_parsed = type(
        "Parsed",
        (),
        {
            "entries": [
                {"title": None, "link": None},
                {
                    "title": "Valid",
                    "link": "https://example.com/v",
                    "published_parsed": time.struct_time((10000, 1, 1, 0, 0, 0, 0, 1, 0)),
                },
            ]
        },
    )()
    httpx_mock.add_response(url="https://rsshub.example.test/none", text="<rss/>")
    with patch("social_info.fetchers.rsshub.feedparser.parse", return_value=fake_parsed):
        async with httpx.AsyncClient() as client:
            items_none = await rsshub.fetch(_src("rsshub", "rh_none", path="/none"), client)
    assert len(items_none) == 1


@pytest.mark.asyncio
async def test_wewe_rss_extreme_year_returns_fallback(httpx_mock, monkeypatch):
    monkeypatch.setenv("WEWE_RSS_URL", "https://wewe.example.test")
    httpx_mock.add_response(
        url="https://wewe.example.test/feeds/ext.atom",
        text=_rss_xml("Mon, 01 Jan 0001 00:00:00 GMT"),
    )
    async with httpx.AsyncClient() as client:
        items_ext = await wewe_rss.fetch(_src("wewe_rss", "w_ext", account_id="ext"), client)
    assert len(items_ext) == 1


@pytest.mark.asyncio
async def test_wewe_rss_gmt_not_shifted_by_local_tz(httpx_mock, monkeypatch):
    monkeypatch.setenv("WEWE_RSS_URL", "https://wewe.example.test")
    httpx_mock.add_response(
        url="https://wewe.example.test/feeds/gmt.atom",
        text=_rss_xml("Sun, 08 Mar 2026 02:30:00 GMT"),
    )
    monkeypatch.setenv("TZ", "America/New_York")
    if hasattr(time, "tzset"):
        time.tzset()
    try:
        async with httpx.AsyncClient() as client:
            items_gmt = await wewe_rss.fetch(_src("wewe_rss", "w_gmt", account_id="gmt"), client)
        assert items_gmt[0].posted_at == datetime(2026, 3, 8, 2, 30, 0)
    finally:
        monkeypatch.delenv("TZ", raising=False)
        if hasattr(time, "tzset"):
            time.tzset()


@pytest.mark.asyncio
async def test_wewe_rss_none_title_link_and_year_10000(httpx_mock, monkeypatch):
    monkeypatch.setenv("WEWE_RSS_URL", "https://wewe.example.test")
    fake_parsed = type(
        "Parsed",
        (),
        {
            "entries": [
                {"title": None, "link": None},
                {
                    "title": "Valid",
                    "link": "https://example.com/v",
                    "published_parsed": time.struct_time((10000, 1, 1, 0, 0, 0, 0, 1, 0)),
                },
            ]
        },
    )()
    httpx_mock.add_response(url="https://wewe.example.test/feeds/none.atom", text="<rss/>")
    with patch("social_info.fetchers.wewe_rss.feedparser.parse", return_value=fake_parsed):
        async with httpx.AsyncClient() as client:
            items_none = await wewe_rss.fetch(_src("wewe_rss", "w_none", account_id="none"), client)
    assert len(items_none) == 1


@pytest.mark.parametrize("ts", [10**15, -(10**15), "not-a-ts", float("inf")])
def test_reddit_post_posted_at_extreme_epoch_returns_fallback(ts):
    fallback = datetime(2026, 11, 21, 0, 0, 0)
    assert reddit._post_posted_at({"created_utc": ts}, fallback) == fallback


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"by": "alice", "text": "hello", "time": 1732147200000},
        {"by": "alice", "text": "hello", "time": -(10**12)},
        "permission denied",
    ],
)
async def test_hn_fetch_one_comment_extreme_epoch_and_non_dict(httpx_mock, payload):
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/123.json",
        json=payload,
    )
    async with httpx.AsyncClient() as client:
        res = await hn._fetch_one_comment(client, 123)
    if isinstance(payload, dict):
        assert res is not None
        assert res["author"] == "alice"
    else:
        assert res is None


@pytest.mark.asyncio
async def test_hn_fetch_null_hits_and_plus0800_utc_conversion(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"https://hn\.algolia\.com/api/v1/search_by_date.*"),
        json={"hits": None},
    )
    async with httpx.AsyncClient() as client:
        items = await hn.fetch(_src("hn_algolia", "hn", keywords=[], limit=10), client)
    assert items == []

    httpx_mock.add_response(
        url=re.compile(r"https://hn\.algolia\.com/api/v1/search_by_date.*"),
        json={
            "hits": [
                {
                    "objectID": "99",
                    "title": "TZ test",
                    "url": "https://example.com/tz",
                    "author": "bob",
                    "points": 10,
                    "num_comments": 0,
                    "created_at": "2026-11-21T08:00:00+08:00",
                }
            ]
        },
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/99.json",
        json={"id": 99, "kids": []},
    )
    async with httpx.AsyncClient() as client:
        items_tz = await hn.fetch(_src("hn_algolia", "hn", keywords=[], limit=10), client)
    assert len(items_tz) == 1
    assert items_tz[0].posted_at == datetime(2026, 11, 21, 0, 0, 0)


@pytest.mark.asyncio
async def test_v2ex_fetch_ms_epoch_and_null_node_name(httpx_mock):
    httpx_mock.add_response(
        url=v2ex.HOT_URL,
        json=[
            {
                "id": 1,
                "title": "V2EX topic",
                "url": "https://www.v2ex.com/t/1",
                "content": "body",
                "created": 1732147200000,
                "replies": 2,
                "node": {"name": None},
                "member": {"username": "tester"},
            }
        ],
    )
    async with httpx.AsyncClient() as client:
        items = await v2ex.fetch(_src("v2ex", "v2ex_hot", node="hot"), client)
    assert len(items) == 1
    assert isinstance(items[0].source_handle, str)
    rendered = render_item(items[0])
    assert "v2ex:" in rendered


@pytest.mark.asyncio
async def test_github_search_null_pushed_at_null_owner_and_tz_conversion(monkeypatch):
    payload = {
        "items": [
            {
                "full_name": "bad/null-pushed",
                "html_url": "https://github.com/bad/null-pushed",
                "description": "missing pushed_at",
                "pushed_at": None,
                "owner": None,
                "stargazers_count": 5,
            },
            {
                "full_name": "good/plus08",
                "html_url": "https://github.com/good/plus08",
                "description": "valid repo",
                "pushed_at": "2026-11-21T08:00:00+08:00",
                "owner": None,
                "stargazers_count": 100,
            },
        ]
    }
    monkeypatch.setattr(github_search, "_gh_search", lambda q, per_page=30: payload)
    items = await github_search.fetch(
        _src("github_search", "gh_search", queries=["ai"], per_query_limit=10)
    )
    by_title = {it.title: it for it in items}
    assert "good/plus08" in by_title
    assert by_title["good/plus08"].posted_at == datetime(2026, 11, 21, 0, 0, 0)
    assert by_title["good/plus08"].author == "good"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"data": None},
        {"data": {"posts": {"edges": None}}},
        {
            "data": {
                "posts": {
                    "edges": [
                        {"node": {"id": "1", "tagline": "no name or url"}},
                        {
                            "node": {
                                "id": "2",
                                "name": "ValidProd",
                                "tagline": "cool",
                                "url": "https://www.producthunt.com/posts/validprod",
                                "createdAt": "2026-11-21T08:00:00+08:00",
                            }
                        },
                    ]
                }
            }
        },
    ],
)
async def test_product_hunt_null_shapes_missing_fields_and_tz(httpx_mock, monkeypatch, body):
    monkeypatch.setenv("PRODUCT_HUNT_TOKEN", "ph_test_token")
    httpx_mock.add_response(url=product_hunt.API_URL, json=body)
    async with httpx.AsyncClient() as client:
        items = await product_hunt.fetch(_src("product_hunt", "ph"), client)
    if body == {"data": None} or body == {"data": {"posts": {"edges": None}}}:
        assert items == []
    else:
        assert len(items) == 1
        assert items[0].posted_at == datetime(2026, 11, 21, 0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [None, {"error": "rate limit"}])
async def test_huggingface_non_list_json_no_crash(httpx_mock, body):
    httpx_mock.add_response(
        url=re.compile(r"https://huggingface\.co/api/models.*"),
        json=body,
    )
    async with httpx.AsyncClient() as client:
        items = await huggingface.fetch(_src("huggingface", "hf", category="models"), client)
    assert items == []


@pytest.mark.asyncio
async def test_huggingface_plus0800_utc_conversion(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"https://huggingface\.co/api/models.*"),
        json=[
            {
                "id": "org/model-1",
                "lastModified": "2026-11-21T08:00:00+08:00",
                "likes": 10,
                "downloads": 20,
            }
        ],
    )
    async with httpx.AsyncClient() as client:
        items = await huggingface.fetch(_src("huggingface", "hf", category="models"), client)
    assert len(items) == 1
    assert items[0].posted_at == datetime(2026, 11, 21, 0, 0, 0)


@pytest.mark.asyncio
async def test_threads_apify_huge_created_at_no_overflow(httpx_mock, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN_THREADS", "apify_test_tok")
    httpx_mock.add_response(
        url=re.compile(r"https://api\.apify\.com/v2/acts/.*"),
        json=[
            {
                "text": "Overflow test post",
                "url": "https://www.threads.net/@u/post/1",
                "author": "u",
                "created_at": 10**20,
            }
        ],
    )
    async with httpx.AsyncClient() as client:
        items = await threads_apify.fetch(
            _src("threads_apify", "threads", queries=["ai"]),
            client,
        )
    assert len(items) == 1
    assert isinstance(items[0].posted_at, datetime)


def test_threads_post_to_item_plus0800_utc_conversion():
    item = threads._post_to_item(
        {
            "id": "1",
            "text": "hello",
            "permalink": "https://www.threads.net/@u/post/1",
            "username": "u",
            "timestamp": "2026-11-21T08:00:00+0800",
        },
        source_tier=1,
        language="en",
        source_handle="keyword:ai",
    )
    assert item.posted_at == datetime(2026, 11, 21, 0, 0, 0)


@pytest.mark.parametrize("bad_count", ["Infinity", "NaN"])
def test_skills_sh_parse_board_infinity_and_nan_no_overflow(bad_count):
    html = (
        f'<a href="/owner/repo/skill">'
        f"<h3>my-skill</h3><p>owner/repo</p>"
        f"<span>1</span><span>{bad_count}</span>"
        f"</a>"
    )
    items = skills_sh._parse_board(html, "trending", tier=1)
    assert items == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"code": 0, "data": []},
        {"code": 0, "data": ["unexpected"]},
        {
            "code": 0,
            "data": {
                "skills": [
                    {
                        "slug": "demo-skill",
                        "name": "Demo",
                        "namespace": "public",
                        "downloads": 10,
                    }
                ]
            },
        },
    ],
)
async def test_skillhub_cn_bad_data_and_string_namespace(httpx_mock, payload):
    httpx_mock.add_response(url=re.compile(r"https://api\.skillhub\.cn/api/skills.*"), json=payload)
    async with httpx.AsyncClient() as client:
        items = await skillhub_cn.fetch(_src("skillhub_cn", "skillhub"), client)
    if isinstance(payload.get("data"), list):
        assert items == []
    else:
        assert len(items) == 1
        assert items[0].url.endswith("/skills/demo-skill")


@pytest.mark.asyncio
async def test_clawhub_dict_next_cursor_no_typeerror(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"https://clawhub\.ai/api/v1/skills.*"),
        json={
            "items": [{"slug": "s1", "displayName": "S1", "stats": {"downloads": 5}}],
            "nextCursor": {"offset": 100},
        },
    )
    async with httpx.AsyncClient() as client:
        items = await clawhub.fetch(_src("clawhub", "clawhub", limit=50), client)
    assert len(items) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        ["unexpected"],
        {"hackathons": [{"title": "Hack", "url": "https://devpost.com/h/1", "prizes_counts": [1, 2]}]},
    ],
)
async def test_devpost_non_dict_payload_and_prizes_counts(httpx_mock, payload):
    httpx_mock.add_response(url=re.compile(r"https://devpost\.com/api/hackathons.*"), json=payload)
    async with httpx.AsyncClient() as client:
        items = await devpost.fetch(_src("devpost", "devpost"), client)
    assert isinstance(items, list)


def test_github_trending_parse_stars_infk_returns_zero():
    assert github_trending._parse_stars("infk") == 0


def test_muse_spool_bad_ts_and_non_dict_record():
    now = datetime(2026, 11, 21, 0, 0, 0)
    spool = {
        "collected_at": "2026-11-21T00:00:00Z",
        "items": [
            "bad-string-record",
            {"url": "https://www.threads.net/@u/post/1", "text": "ok", "ts": "not-a-timestamp"},
        ],
    }
    items = muse_spool.items_from_spool(spool, _src("muse_spool", "threads"), now)
    assert len(items) == 1
    assert items[0].posted_at == now


@pytest.mark.parametrize("raw_text", ["{broken json", "", "null"])
def test_free_radar_load_config_malformed_json_no_crash(tmp_path: Path, raw_text: str):
    cfg_path = tmp_path / "radar.json"
    cfg_path.write_text(raw_text, encoding="utf-8")
    cfg, notes = free_radar._load_config(cfg_path)
    assert cfg["lang"] == "en"
    assert isinstance(notes, list)


def test_free_radar_to_items_bad_created_at_no_crash():
    report = {
        "captured_at": "2026-11-21T00:00:00Z",
        "raw_items": [
            {
                "kind": "github",
                "title": "org/repo",
                "url": "https://github.com/org/repo",
                "created_at": "not-an-iso-date",
                "status": "lead",
            }
        ],
    }
    items = free_radar.to_items(report)
    assert len(items) == 1
    assert items[0].posted_at == datetime(2026, 11, 21, 0, 0, 0)


# ---------------------------------------------------------------------------
# 5. redact.py
# ---------------------------------------------------------------------------


def test_redact_item_masks_nested_list_and_dict_in_metadata():
    sk = _fake_sk()
    gh = _fake_gh()
    item = Item(
        title="t",
        url="https://example.com/p",
        canonical_url="https://example.com/p",
        source="new_term_radar",
        source_handle="hf:models",
        source_tier=2,
        posted_at=datetime(2026, 11, 21, 0, 0, 0),
        fetched_at=datetime(2026, 11, 21, 0, 0, 0),
        metadata={
            "tags": [f"tag-{sk}", "safe"],
            "nested": {"deep_token": f"val {gh}"},
        },
    )
    out = redact_item(item)
    assert sk not in repr(out.metadata)
    assert gh not in repr(out.metadata)
    assert "[REDACTED-KEY:sk]" in out.metadata["tags"][0]
    assert "[REDACTED-KEY:github]" in out.metadata["nested"]["deep_token"]


# ---------------------------------------------------------------------------
# 6. markdown.py
# ---------------------------------------------------------------------------


def _sample_item(**overrides) -> Item:
    base = dict(
        title="Sample title",
        url="https://example.com/post",
        canonical_url="https://example.com/post",
        source="v2ex",
        source_handle="python",
        source_tier=1,
        posted_at=datetime(2026, 11, 21, 0, 0, 0),
        fetched_at=datetime(2026, 11, 21, 0, 0, 0),
    )
    base.update(overrides)
    return Item(**base)


def test_render_item_none_source_handle_does_not_raise():
    item = _sample_item(author="alice")
    item.source_handle = None  # type: ignore[assignment]
    out = render_item(item)
    assert "v2ex:" in out


def test_render_item_escapes_brackets_in_title_and_blocks_unsafe_urls():
    # Title containing markdown link syntax and unbalanced brackets
    item_brackets = _sample_item(title="foo [a](b) and unbalanced ] [ bar")
    out_brackets = render_item(item_brackets)
    first_line = out_brackets.splitlines()[0]
    # Must not produce an unescaped inner [a](b) link break inside the heading link
    assert "[a](b)" not in first_line

    # URL containing ) or space
    item_paren_url = _sample_item(url="https://en.wikipedia.org/wiki/Foo_(bar) baz")
    out_paren = render_item(item_paren_url)
    heading_paren = out_paren.splitlines()[0]
    assert " baz)" not in heading_paren
    assert "(bar))" not in heading_paren

    # Unsafe javascript: URL
    item_js_url = _sample_item(url="javascript:alert(1)")
    out_js = render_item(item_js_url)
    assert "](javascript:" not in out_js
    assert "(javascript:alert(1))" not in out_js


def test_render_item_strips_nul_and_normalizes_crlf_in_linuxdo_and_comments():
    item = _sample_item(
        source="linuxdo",
        source_handle="dev",
        excerpt="line1\x00\r\nline2\rline3",
        content="body1\x00\r\nbody2\rbody3",
        comments=[{"author": "a\x00b", "text": "c1\x00\r\nc2\rc3"}],
        metadata={"content_kind": "post_text"},
    )
    out = render_item(item)
    assert "\x00" not in out
    assert "\r" not in out


def test_splice_radar_section_preserves_literal_markers_in_post_body():
    body_literal = f"Post mentions {RADAR_START}foo{RADAR_END} literally."
    existing_doc = (
        f"# AI Daily Digest — 2026-11-21\n\n"
        f"{body_literal}\n\n"
        f"{RADAR_START}\nold radar content\n{RADAR_END}\n"
    )
    updated = splice_radar_section(existing_doc, "## New Radar\n- item 1\n")
    assert body_literal in updated
    assert "old radar content" not in updated
    assert "## New Radar" in updated


def test_trendshift_unescape_js_chunk_lone_surrogate_stays_utf8_encodable():
    decoded = trendshift._unescape_js_chunk(r"hello \uD800 world")
    encoded = decoded.encode("utf-8")
    assert b"hello " in encoded and b" world" in encoded


def test_redact_preserves_lowercase_sk_vendor_prose_slugs():
    from social_info.redact import redact_text

    samples = [
        "https://example.com/blog/my-sk-admin-dashboard-guide",
        "check-sk-proj-documentation-v2",
        "tag-sk-ant-colony-optimization",
        "my-sk-svcacct-rotation-guide",
    ]
    for sample in samples:
        assert redact_text(sample) == sample


def test_render_item_escapes_trailing_backslash_and_encodes_open_paren_in_url():
    item = _sample_item(
        title="trailing\\",
        url="https://en.wikipedia.org/wiki/Python_(programming_language)",
    )
    first_line = render_item(item).splitlines()[0]
    assert first_line == (
        "### [trailing\\\\](https://en.wikipedia.org/wiki/Python_%28programming_language%29)"
    )


def test_twitter_non_string_text_and_non_dict_rows_guarded():
    from social_info.fetchers import twitter

    now = datetime(2026, 11, 21, 0, 0, 0)
    rows = [
        "not-a-dict",
        {"id": "1", "text": ["list-not-str"], "username": "u"},
        {"id": "2", "text": "valid tweet", "full_text": 123, "username": "u"},
    ]
    items = twitter._to_items(rows, _src("x", "twitter"), now)
    assert len(items) == 1
    assert items[0].title == "valid tweet"


