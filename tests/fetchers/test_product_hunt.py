import json
import re
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.product_hunt import fetch


@pytest.mark.asyncio
async def test_fetch_product_hunt(httpx_mock, monkeypatch):
    monkeypatch.setenv("PRODUCT_HUNT_TOKEN", "fake-token")
    fixture = json.loads(Path("tests/fixtures/product_hunt_response.json").read_text())
    httpx_mock.add_response(
        url=re.compile(r"https://api\.producthunt\.com.*"),
        json=fixture,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="product_hunt",
        type="product_hunt",
        enabled=True,
        tier=2,
        params={"topic": "artificial-intelligence", "limit": 10},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) == 1
    item = items[0]
    assert item.source == "product_hunt"
    assert "ClaudeCraft" in item.title
    assert item.engagement["votes"] == 234


@pytest.mark.asyncio
async def test_fetch_product_hunt_no_token_raises(monkeypatch):
    monkeypatch.delenv("PRODUCT_HUNT_TOKEN", raising=False)
    cfg = SourceConfig(
        id="product_hunt",
        type="product_hunt",
        enabled=True,
        tier=2,
        params={},
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(RuntimeError, match="PRODUCT_HUNT_TOKEN"):
            await fetch(cfg, client)
