"""Two-tier dedup: L1 by canonical URL, L2 by normalized title hash."""
import hashlib
import json
import re
import unicodedata

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


class Deduper:
    """Filters incoming Items against the items table.

    Returns the list of Items that should be persisted as NEW rows.
    For L2 collisions: if incoming is higher tier (lower number) than the
    stored one, incoming wins and the existing record's source is merged
    into the new item's also_appeared_in. If incoming is lower tier or
    equal, it gets merged into the existing row's also_appeared_in instead.
    """

    def __init__(self, db: Database):
        self.db = db

    def process(self, items: list[Item]) -> list[Item]:
        new_items: list[Item] = []
        seen_ids_in_batch: set[str] = set()
        seen_title_hashes_in_batch: dict[str, Item] = {}

        for item in items:
            item_id = compute_item_id(item.canonical_url)
            title_hash = compute_title_hash(item.title)

            if item_id in seen_ids_in_batch:
                continue
            if self.db.has_item_id(item_id):
                continue

            existing = self.db.find_by_title_hash(title_hash)
            if existing is not None:
                if item.source_tier < existing["source_tier"]:
                    item.also_appeared_in.append({
                        "source": existing["source"],
                        "source_handle": existing["source_handle"] or "",
                        "url": existing["url"],
                    })
                    new_items.append(item)
                    seen_ids_in_batch.add(item_id)
                    seen_title_hashes_in_batch[title_hash] = item
                else:
                    appeared = json.loads(existing["also_appeared_in"] or "[]")
                    appeared.append({
                        "source": item.source,
                        "source_handle": item.source_handle,
                        "url": item.url,
                    })
                    self.db.update_also_appeared_in(existing["id"], json.dumps(appeared))
                continue

            if title_hash in seen_title_hashes_in_batch:
                prior = seen_title_hashes_in_batch[title_hash]
                if item.source_tier < prior.source_tier:
                    new_items.remove(prior)
                    item.also_appeared_in.append({
                        "source": prior.source,
                        "source_handle": prior.source_handle,
                        "url": prior.url,
                    })
                    new_items.append(item)
                    seen_title_hashes_in_batch[title_hash] = item
                    seen_ids_in_batch.add(item_id)
                else:
                    prior.also_appeared_in.append({
                        "source": item.source,
                        "source_handle": item.source_handle,
                        "url": item.url,
                    })
                continue

            new_items.append(item)
            seen_ids_in_batch.add(item_id)
            seen_title_hashes_in_batch[title_hash] = item

        return new_items
