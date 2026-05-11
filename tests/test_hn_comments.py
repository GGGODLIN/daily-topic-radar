"""Test HN fetcher's Firebase comment enrichment path."""
import re

import httpx
from pytest_httpx import HTTPXMock

from social_info._time import utcfromtimestamp
from social_info.config import SourceConfig
from social_info.fetchers import hn
from social_info.fetchers.hn import COMMENT_MAX_CHARS


def _algolia_response(story_id: str = "48087925", title: str = "Test story") -> dict:
    return {
        "hits": [
            {
                "objectID": story_id,
                "title": title,
                "url": "https://example.com/article",
                "author": "tester",
                "created_at": "2026-05-10T20:57:00.000Z",
                "points": 6,
                "num_comments": 2,
            }
        ]
    }


def _firebase_story(story_id: str, kid_ids: list[int]) -> dict:
    return {
        "id": int(story_id),
        "type": "story",
        "by": "tester",
        "title": "Test story",
        "url": "https://example.com/article",
        "kids": kid_ids,
        "time": 1747000000,
    }


def _firebase_comment(comment_id: int, by: str, text: str, *, deleted: bool = False, dead: bool = False) -> dict:
    base = {"id": comment_id, "type": "comment", "time": 1747000100}
    if deleted:
        base["deleted"] = True
        return base
    if dead:
        base["dead"] = True
    base["by"] = by
    base["text"] = text
    return base


def _source() -> SourceConfig:
    return SourceConfig(
        id="hn_algolia",
        type="hn_algolia",
        tier=1,
        enabled=True,
        language="en",
        params={"limit": 5},
    )


async def test_fetch_attaches_top5_comments_to_item(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=re.compile(r"https://hn\.algolia\.com/api/v1/search_by_date"),
        json=_algolia_response("100"),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/100.json",
        json=_firebase_story("100", [201, 202, 203, 204, 205, 206]),
    )
    for cid, by, text in [
        (201, "alice", "first"),
        (202, "bob", "second"),
        (203, "carol", "third"),
        (204, "dave", "fourth"),
        (205, "eve", "fifth"),
    ]:
        httpx_mock.add_response(
            url=f"https://hacker-news.firebaseio.com/v0/item/{cid}.json",
            json=_firebase_comment(cid, by, text),
        )

    async with httpx.AsyncClient() as http:
        items = await hn.fetch(_source(), http)

    assert len(items) == 1
    item = items[0]
    assert len(item.comments) == 5
    expected_posted_at = (
        utcfromtimestamp(1747000100).replace(microsecond=0).isoformat()
    )
    assert item.comments[0] == {
        "author": "alice",
        "text": "first",
        "posted_at": expected_posted_at,
    }
    assert [c["author"] for c in item.comments] == ["alice", "bob", "carol", "dave", "eve"]


async def test_fetch_skips_deleted_and_dead_comments(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=re.compile(r"https://hn\.algolia\.com/api/v1/search_by_date"),
        json=_algolia_response("101"),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/101.json",
        json=_firebase_story("101", [301, 302, 303]),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/301.json",
        json=_firebase_comment(301, "?", "?", deleted=True),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/302.json",
        json=_firebase_comment(302, "?", "junk", dead=True),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/303.json",
        json=_firebase_comment(303, "alive", "real comment"),
    )

    async with httpx.AsyncClient() as http:
        items = await hn.fetch(_source(), http)

    assert len(items[0].comments) == 1
    assert items[0].comments[0]["author"] == "alive"


async def test_fetch_continues_when_one_kid_call_fails(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=re.compile(r"https://hn\.algolia\.com/api/v1/search_by_date"),
        json=_algolia_response("102"),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/102.json",
        json=_firebase_story("102", [401, 402, 403]),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/401.json",
        json=_firebase_comment(401, "ok1", "first"),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/402.json",
        status_code=500,
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/403.json",
        json=_firebase_comment(403, "ok2", "third"),
    )

    async with httpx.AsyncClient() as http:
        items = await hn.fetch(_source(), http)

    authors = [c["author"] for c in items[0].comments]
    assert authors == ["ok1", "ok2"]


async def test_fetch_handles_story_with_no_kids(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=re.compile(r"https://hn\.algolia\.com/api/v1/search_by_date"),
        json=_algolia_response("103"),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/103.json",
        json={"id": 103, "type": "story", "by": "x", "title": "no kids", "time": 1747000000},
    )

    async with httpx.AsyncClient() as http:
        items = await hn.fetch(_source(), http)

    assert items[0].comments == []


async def test_fetch_handles_story_call_failure(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=re.compile(r"https://hn\.algolia\.com/api/v1/search_by_date"),
        json=_algolia_response("104"),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/104.json",
        status_code=500,
    )

    async with httpx.AsyncClient() as http:
        items = await hn.fetch(_source(), http)

    assert len(items) == 1
    assert items[0].comments == []


async def test_fetch_unescapes_html_entities(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=re.compile(r"https://hn\.algolia\.com/api/v1/search_by_date"),
        json=_algolia_response("106"),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/106.json",
        json=_firebase_story("106", [601]),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/601.json",
        json=_firebase_comment(601, "user", "Windows said &quot;protected your PC&quot; &amp; failed."),
    )

    async with httpx.AsyncClient() as http:
        items = await hn.fetch(_source(), http)

    assert items[0].comments[0]["text"] == 'Windows said "protected your PC" & failed.'


async def test_fetch_strips_html_and_trims_text(httpx_mock: HTTPXMock):
    long = "x" * (COMMENT_MAX_CHARS + 200)
    httpx_mock.add_response(
        url=re.compile(r"https://hn\.algolia\.com/api/v1/search_by_date"),
        json=_algolia_response("105"),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/105.json",
        json=_firebase_story("105", [501, 502]),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/501.json",
        json=_firebase_comment(501, "html_user", "<p>Hello <a href='x'>world</a></p>"),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/502.json",
        json=_firebase_comment(502, "long_user", f"<p>{long}</p>"),
    )

    async with httpx.AsyncClient() as http:
        items = await hn.fetch(_source(), http)

    texts = {c["author"]: c["text"] for c in items[0].comments}
    assert texts["html_user"] == "Hello world"
    assert texts["long_user"] == "x" * COMMENT_MAX_CHARS + "…"
