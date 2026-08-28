"""Two-tier dedup: L1 by canonical URL, L2 by normalized title hash."""
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from social_info._time import utcnow
from social_info.db import Database
from social_info.fetchers.base import Item

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """NFKC + lowercase + strip Unicode punctuation + collapse whitespace."""
    t = unicodedata.normalize("NFKC", title)
    t = t.lower()
    t = _PUNCT_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    return t


def compute_item_id(canonical_url: str) -> str:
    return hashlib.sha1(canonical_url.encode("utf-8")).hexdigest()


def compute_title_hash(title: str) -> str:
    return hashlib.sha1(normalize_title(title).encode("utf-8")).hexdigest()


@dataclass
class DedupResult:
    new_items: list[Item] = field(default_factory=list)
    resurface_ids: list[str] = field(default_factory=list)


class Deduper:
    """Filters incoming Items against the items table.

    Returns a DedupResult: new_items to persist as NEW rows, plus
    resurface_ids for existing rows whose last_surfaced_at should be
    bumped to now (they are ready to be shown again after resurface_days).

    For L2 collisions: if incoming is higher tier (lower number) than the
    stored one, incoming wins and the existing record's source is merged
    into the new item's also_appeared_in. If incoming is lower tier or
    equal, it gets merged into the existing row's also_appeared_in instead.
    Either branch also resurfaces the existing row if it is stale.

    For L1 collisions (same canonical URL already stored): stale rows
    resurface, fresh rows are skipped entirely.
    """

    def __init__(self, db: Database, resurface_days: int = 30):
        self.db = db
        self.resurface_days = resurface_days

    def process(self, items: list[Item]) -> DedupResult:
        result = DedupResult()
        seen_ids_in_batch: set[str] = set()
        seen_items_by_id: dict[str, Item] = {}
        seen_title_hashes_in_batch: dict[str, Item] = {}
        threshold = utcnow() - timedelta(days=self.resurface_days)

        for item in items:
            item_id = compute_item_id(item.canonical_url)
            title_hash = compute_title_hash(item.title)

            if item_id in seen_ids_in_batch:
                prior = seen_items_by_id.get(item_id)
                if prior is not None:
                    prior.also_appeared_in.append({
                        "source": item.source,
                        "source_handle": item.source_handle,
                        "url": item.url,
                    })
                continue

            if self.db.has_item_id(item_id):
                last_surfaced = self._get_last_surfaced_at(item_id)
                if last_surfaced is not None and last_surfaced < threshold:
                    result.resurface_ids.append(item_id)
                    seen_ids_in_batch.add(item_id)
                continue

            existing = self.db.find_by_title_hash(title_hash)
            if existing is not None:
                existing_last = self._parse_dt(existing.get("last_surfaced_at"))
                is_stale = existing_last is not None and existing_last < threshold

                if item.source_tier < existing["source_tier"]:
                    item.also_appeared_in.append({
                        "source": existing["source"],
                        "source_handle": existing["source_handle"] or "",
                        "url": existing["url"],
                    })
                    result.new_items.append(item)
                    seen_ids_in_batch.add(item_id)
                    seen_items_by_id[item_id] = item
                    seen_title_hashes_in_batch[title_hash] = item
                    if is_stale:
                        result.resurface_ids.append(existing["id"])
                else:
                    appeared = json.loads(existing["also_appeared_in"] or "[]")
                    appeared.append({
                        "source": item.source,
                        "source_handle": item.source_handle,
                        "url": item.url,
                    })
                    self.db.update_also_appeared_in(existing["id"], json.dumps(appeared))
                    if is_stale:
                        result.resurface_ids.append(existing["id"])
                continue

            if title_hash in seen_title_hashes_in_batch:
                prior = seen_title_hashes_in_batch[title_hash]
                if item.source_tier < prior.source_tier:
                    result.new_items.remove(prior)
                    item.also_appeared_in.append({
                        "source": prior.source,
                        "source_handle": prior.source_handle,
                        "url": prior.url,
                    })
                    result.new_items.append(item)
                    seen_title_hashes_in_batch[title_hash] = item
                    seen_ids_in_batch.add(item_id)
                    seen_items_by_id[item_id] = item
                    seen_items_by_id[compute_item_id(prior.canonical_url)] = item
                else:
                    prior.also_appeared_in.append({
                        "source": item.source,
                        "source_handle": item.source_handle,
                        "url": item.url,
                    })
                continue

            result.new_items.append(item)
            seen_ids_in_batch.add(item_id)
            seen_items_by_id[item_id] = item
            seen_title_hashes_in_batch[title_hash] = item

        return result

    def _get_last_surfaced_at(self, item_id: str) -> datetime | None:
        cur = self.db.conn.execute(
            "SELECT last_surfaced_at FROM items WHERE id = ?", (item_id,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return self._parse_dt(row["last_surfaced_at"])

    @staticmethod
    def _parse_dt(s: str | None) -> datetime | None:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, TypeError):
            return None
