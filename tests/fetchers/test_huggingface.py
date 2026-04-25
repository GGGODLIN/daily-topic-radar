import json
import re
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.huggingface import fetch


@pytest.mark.asyncio
async def test_fetch_hf_models(httpx_mock):
    fixture = json.loads(Path("tests/fixtures/hf_models.json").read_text())
    httpx_mock.add_response(
        url=re.compile(r"https://huggingface\.co/api/models.*"),
        json=fixture,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="huggingface_models",
        type="huggingface",
        enabled=True,
        tier=2,
        params={"category": "models", "limit": 10},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert all(it.source == "huggingface" for it in items)
    assert all("huggingface.co" in it.url for it in items)
