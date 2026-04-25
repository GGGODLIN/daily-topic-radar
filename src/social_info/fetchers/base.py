"""Core types shared by all fetchers."""
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Item:
    """Normalized item ready for dedup + markdown rendering."""

    title: str
    url: str
    canonical_url: str
    source: str
    source_handle: str
    source_tier: int
    posted_at: datetime
    fetched_at: datetime
    author: str = ""
    excerpt: str = ""
    language: str = "en"
    engagement: dict[str, int] = field(default_factory=dict)
    also_appeared_in: list[dict[str, str]] = field(default_factory=list)

    def to_db_row(self, item_id: str, title_hash: str) -> dict[str, Any]:
        return {
            "id": item_id,
            "url": self.url,
            "canonical_url": self.canonical_url,
            "title": self.title,
            "title_hash": title_hash,
            "source": self.source,
            "source_handle": self.source_handle,
            "source_tier": self.source_tier,
            "posted_at": self.posted_at.isoformat(),
            "fetched_at": self.fetched_at.isoformat(),
            "author": self.author,
            "excerpt": self.excerpt,
            "language": self.language,
            "engagement_json": json.dumps(self.engagement),
            "also_appeared_in": json.dumps(self.also_appeared_in),
        }


@dataclass
class FetchResult:
    """Result of one fetcher run."""

    source_id: str
    items: list[Item] = field(default_factory=list)
    ok: bool = True
    error: str = ""
    started_at: datetime = field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None

    def items_count(self) -> int:
        return len(self.items)
