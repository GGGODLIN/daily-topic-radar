"""GitHub search API fetcher — repositories by description + name + topic.

Discovery-oriented complement to github_trending (which only shows
rising-period repos). Uses `gh api` CLI wrapper for auth (reuses existing
`gh auth` token, no separate credential management).
"""
import asyncio
import json
import re
import subprocess
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from social_info._time import utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

_DATE_TEMPLATE_RE = re.compile(r"\{(\d+)d\}")


def _substitute_template(query: str, now: datetime | None = None) -> str:
    """Replace {Nd} with (now - N days) in YYYY-MM-DD format."""
    now = now or utcnow()

    def repl(match: re.Match) -> str:
        days = int(match.group(1))
        target = now - timedelta(days=days)
        return target.strftime("%Y-%m-%d")

    return _DATE_TEMPLATE_RE.sub(repl, query)


def _gh_search(query: str, per_page: int = 30) -> dict[str, Any]:
    """Sync gh api call. Kept out of async path via asyncio.to_thread.

    Unencoded spaces in `q` make `gh api` hang indefinitely instead of
    erroring (verified 2026-07-09) — urlencode is required, not cosmetic.
    """
    qs = urlencode({"q": query, "per_page": per_page})
    result = subprocess.run(
        ["gh", "api", f"/search/repositories?{qs}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(result.stdout)


async def fetch(source: SourceConfig, http=None) -> list[Item]:
    queries = source.params.get("queries", [])
    per_query_limit = source.params.get("per_query_limit", 30)

    now = utcnow()
    items: list[Item] = []
    seen_urls: set[str] = set()

    for query_template in queries:
        query = _substitute_template(query_template, now=now)
        try:
            data = await asyncio.to_thread(_gh_search, query, per_query_limit)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"gh search failed: {e.stderr}") from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"gh search timed out: {e}") from e

        for repo in data.get("items", []):
            full_name = repo["full_name"]
            url = repo["html_url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)

            desc = repo.get("description") or ""
            pushed_at = datetime.fromisoformat(
                repo["pushed_at"].replace("Z", "+00:00")
            ).replace(tzinfo=None)

            items.append(Item(
                title=full_name,
                url=url,
                canonical_url=canonical_url(url),
                source="github_search",
                source_handle=f"query:{query[:50]}",
                source_tier=source.tier,
                posted_at=pushed_at,
                fetched_at=now,
                author=repo.get("owner", {}).get("login", full_name.split("/")[0]),
                excerpt=desc[:200],
                language="en",
                engagement={"stars": repo.get("stargazers_count", 0)},
            ))

    return items
