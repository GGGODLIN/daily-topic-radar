"""Core types shared by all fetchers."""
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from social_info._time import utcnow


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
    comments: list[dict[str, str]] = field(default_factory=list)
    # Full text the source already returned. Not a page fetch, and not stored
    # in state.db — to_db_row omits it so tracked items need no migration.
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def body_text(self) -> str:
        return self.content or self.excerpt or self.title

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
            "comments_json": json.dumps(self.comments),
        }


@dataclass
class FetchResult:
    """Result of one fetcher run."""

    source_id: str
    items: list[Item] = field(default_factory=list)
    ok: bool = True
    error: str = ""
    error_class: str = ""
    attempts: int = 1
    started_at: datetime = field(default_factory=utcnow)
    ended_at: datetime | None = None
    net_new: int | None = None

    def items_count(self) -> int:
        return len(self.items)
