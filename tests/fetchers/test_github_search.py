import json
from pathlib import Path
from unittest.mock import patch

import pytest

from social_info.config import SourceConfig
from social_info.fetchers.github_search import fetch


@pytest.mark.asyncio
async def test_fetch_github_search_parses_response():
    fixture_path = Path("tests/fixtures/github_search_response.json")
    fixture_data = json.loads(fixture_path.read_text())

    cfg = SourceConfig(
        id="github_search",
        type="github_search",
        enabled=True,
        tier=1,
        params={
            "queries": [
                "stars:>10000 claude in:description pushed:>{7d}",
            ],
            "per_query_limit": 30,
        },
    )

    with patch("social_info.fetchers.github_search._gh_search") as mock:
        mock.return_value = fixture_data
        items = await fetch(cfg)

    assert len(items) > 0
    assert all(it.source == "github_search" for it in items)
    assert all("github.com" in it.url for it in items)
    assert all(it.engagement.get("stars", 0) > 0 for it in items)


def test_substitute_query_template_date():
    from datetime import datetime

    from social_info.fetchers.github_search import _substitute_template

    now = datetime(2026, 7, 9)
    result = _substitute_template(
        "stars:>1000 claude in:description pushed:>{7d}", now=now
    )
    assert result == "stars:>1000 claude in:description pushed:>2026-07-02"
