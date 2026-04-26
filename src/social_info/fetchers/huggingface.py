"""HuggingFace Hub trending models / spaces fetcher."""
from datetime import datetime

import httpx

from social_info._time import utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    category = source.params.get("category", "models")
    limit = source.params.get("limit", 10)
    url = f"https://huggingface.co/api/{category}"
    resp = await http.get(
        url,
        params={"sort": "likes7d", "direction": -1, "limit": limit},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()

    items: list[Item] = []
    now = utcnow()
    for entry in data:
        repo_id = entry.get("id") or entry.get("modelId") or ""
        if not repo_id:
            continue
        full_url = f"https://huggingface.co/{repo_id}"
        title = repo_id
        try:
            posted_at = datetime.fromisoformat(
                entry["lastModified"].replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except (KeyError, ValueError):
            posted_at = now
        items.append(Item(
            title=title,
            url=full_url,
            canonical_url=canonical_url(full_url),
            source="huggingface",
            source_handle=f"trending:{category}",
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=repo_id.split("/")[0] if "/" in repo_id else "",
            excerpt=entry.get("pipeline_tag", "") or "",
            language="en",
            engagement={
                "likes": int(entry.get("likes") or 0),
                "downloads": int(entry.get("downloads") or 0),
            },
        ))
    return items
