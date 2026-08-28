from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.skills_sh import fetch


@pytest.mark.asyncio
async def test_fetch_skills_sh_parses_trending_and_hot(httpx_mock):
    html = Path("tests/fixtures/skills_sh.html").read_text()
    httpx_mock.add_response(url="https://skills.sh/trending", text=html)
    httpx_mock.add_response(url="https://skills.sh/hot", text=html)
    cfg = SourceConfig(
        id="skills_sh",
        type="skills_sh",
        enabled=True,
        tier=1,
        params={"limit": 25},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) == 3
    assert [item.source_handle for item in items] == [
        "trending:rank-1",
        "trending:rank-2",
        "trending:rank-3",
    ]
    assert items[0].title == "alpha-skill"
    assert items[0].url == "https://skills.sh/owner/repo/alpha-skill"
    assert items[0].author == "owner/repo"
    assert items[0].engagement == {"rank": 1, "installs": 21600}
    assert items[0].also_appeared_in == [
        {
            "source": "skills_sh",
            "source_handle": "hot:rank-1",
            "url": "https://skills.sh/owner/repo/alpha-skill",
        }
    ]
    assert items[2].engagement == {"rank": 3, "installs": 92}


@pytest.mark.asyncio
async def test_fetch_skills_sh_respects_shared_limit(httpx_mock):
    html = Path("tests/fixtures/skills_sh.html").read_text()
    httpx_mock.add_response(url="https://skills.sh/trending", text=html)
    httpx_mock.add_response(url="https://skills.sh/hot", text=html)
    cfg = SourceConfig(
        id="skills_sh",
        type="skills_sh",
        enabled=True,
        tier=1,
        params={"limit": 1},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert [item.source_handle for item in items] == ["trending:rank-1"]
    assert items[0].also_appeared_in[0]["source_handle"] == "hot:rank-1"


@pytest.mark.asyncio
async def test_fetch_skills_sh_keeps_hot_when_trending_fails(httpx_mock):
    html = Path("tests/fixtures/skills_sh.html").read_text()
    httpx_mock.add_exception(
        httpx.ConnectError("boom"),
        url="https://skills.sh/trending",
    )
    httpx_mock.add_response(url="https://skills.sh/hot", text=html)
    cfg = SourceConfig(
        id="skills_sh",
        type="skills_sh",
        enabled=True,
        tier=1,
        params={"limit": 25},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) == 3
    assert all(item.source_handle.startswith("hot:rank-") for item in items)


@pytest.mark.asyncio
async def test_fetch_skills_sh_returns_empty_for_unrecognized_html(httpx_mock):
    httpx_mock.add_response(url="https://skills.sh/trending", text="<html></html>")
    httpx_mock.add_response(url="https://skills.sh/hot", text="<html></html>")
    cfg = SourceConfig(
        id="skills_sh",
        type="skills_sh",
        enabled=True,
        tier=1,
        params={"limit": 25},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert items == []


@pytest.mark.asyncio
async def test_fetch_skills_sh_raises_when_all_boards_fail(httpx_mock):
    httpx_mock.add_exception(
        httpx.ConnectError("trending down"),
        url="https://skills.sh/trending",
    )
    httpx_mock.add_exception(
        httpx.ConnectError("hot down"),
        url="https://skills.sh/hot",
    )
    cfg = SourceConfig(
        id="skills_sh",
        type="skills_sh",
        enabled=True,
        tier=1,
        params={"limit": 25},
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.ConnectError, match="hot down"):
            await fetch(cfg, client)
