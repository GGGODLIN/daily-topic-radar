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
from social_info.url_utils import canonical_url

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")
# Emoji- or punctuation-only posts normalize to "" and share one sha1, which
# otherwise swallows unrelated posts across every source.
_MIN_L2_TITLE_LEN = 2


def normalize_title(title: str) -> str:
    """NFKC + lowercase + strip Unicode punctuation + collapse whitespace."""
    t = unicodedata.normalize("NFKC", title)
    t = t.lower()
    t = _PUNCT_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    return t


_METRIC_KEYS = ("downloads", "installs", "stars")


def _metrics_summary(engagement: dict) -> str:
    """Compact metric string kept on merge so cross-registry counts survive dedup."""
    return " / ".join(
        f"{engagement[k]:,} {k}" for k in _METRIC_KEYS if engagement.get(k)
    )


def _row_metrics(row: dict) -> str:
    try:
        return _metrics_summary(json.loads(row.get("engagement_json") or "{}"))
    except (ValueError, TypeError):
        return ""


def _appearance_key(source: str, url: str) -> tuple[str, str]:
    if not url:
        return (source, "")
    try:
        norm_url = canonical_url(url)
    except ValueError:
        # DB rows masked by redact_row may carry "[REDACTED-KEY:...]" in hostnames,
        # which makes urlsplit reject them as malformed IPv6 literals.
        norm_url = ""
    return (source, norm_url or url.strip())


def _merge_appearances(
    existing_entries: list[dict],
    incoming_entries: list[dict],
    *,
    owner_source: str = "",
    owner_url: str = "",
) -> tuple[list[dict], bool]:
    owner_key = _appearance_key(owner_source, owner_url) if owner_source else None
    merged: list[dict] = []
    index_by_key: dict[tuple[str, str], int] = {}

    for raw in [*existing_entries, *incoming_entries]:
        src = raw.get("source") or ""
        url = raw.get("url") or ""
        key = _appearance_key(src, url)
        if owner_key is not None and key == owner_key:
            continue
        clean = {
            "source": src,
            "source_handle": raw.get("source_handle") or "",
            "url": url,
            "metrics": raw.get("metrics") or "",
        }
        if key in index_by_key:
            cur = merged[index_by_key[key]]
            if clean["source_handle"]:
                cur["source_handle"] = clean["source_handle"]
            if clean["metrics"]:
                cur["metrics"] = clean["metrics"]
        else:
            index_by_key[key] = len(merged)
            merged.append(clean)

    return merged, merged != existing_entries


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

    def __init__(self, db: Database, resurface_days: int = 30, *, dry_run: bool = False):
        self.db = db
        self.resurface_days = resurface_days
        self.dry_run = dry_run

    def process(self, items: list[Item]) -> DedupResult:
        result = DedupResult()
        seen_ids_in_batch: dict[str, Item] = {}
        resurfaced_in_batch: set[str] = set()
        seen_title_hashes_in_batch: dict[str, Item] = {}
        threshold = utcnow() - timedelta(days=self.resurface_days)

        for item in items:
            item_id = compute_item_id(item.canonical_url)
            norm_title = normalize_title(item.title)
            can_l2 = len(norm_title) >= _MIN_L2_TITLE_LEN
            title_hash = compute_title_hash(item.title)

            if item_id in seen_ids_in_batch:
                batch_winner = seen_ids_in_batch[item_id]
                if item.source != batch_winner.source:
                    batch_winner.also_appeared_in, _ = _merge_appearances(
                        batch_winner.also_appeared_in,
                        [{
                            "source": item.source,
                            "source_handle": item.source_handle,
                            "url": item.url,
                            "metrics": _metrics_summary(item.engagement),
                        }],
                        owner_source=batch_winner.source,
                        owner_url=batch_winner.canonical_url,
                    )
                continue

            if self.db.has_item_id(item_id):
                rows = self.db.get_items_by_ids([item_id])
                existing_row = rows[0] if rows else None
                if existing_row is not None and item.source != existing_row["source"]:
                    prior_appeared = json.loads(existing_row.get("also_appeared_in") or "[]")
                    merged_appeared, changed = _merge_appearances(
                        prior_appeared,
                        [{
                            "source": item.source,
                            "source_handle": item.source_handle,
                            "url": item.url,
                            "metrics": _metrics_summary(item.engagement),
                        }],
                        owner_source=existing_row["source"],
                        owner_url=existing_row["canonical_url"],
                    )
                    if changed and not self.dry_run:
                        self.db.update_also_appeared_in(
                            item_id, json.dumps(merged_appeared)
                        )
                last_surfaced = (
                    self._parse_dt(existing_row.get("last_surfaced_at"))
                    if existing_row is not None
                    else self._get_last_surfaced_at(item_id)
                )
                if (
                    last_surfaced is not None
                    and last_surfaced < threshold
                    and item_id not in resurfaced_in_batch
                ):
                    result.resurface_ids.append(item_id)
                    resurfaced_in_batch.add(item_id)
                continue

            existing = self.db.find_by_title_hash(title_hash) if can_l2 else None
            if existing is not None:
                existing_last = self._parse_dt(existing.get("last_surfaced_at"))
                is_stale = existing_last is not None and existing_last < threshold
                prior_appeared = json.loads(existing["also_appeared_in"] or "[]")

                if item.source_tier < existing["source_tier"]:
                    item.also_appeared_in, _ = _merge_appearances(
                        item.also_appeared_in,
                        [
                            {
                                "source": existing["source"],
                                "source_handle": existing["source_handle"] or "",
                                "url": existing["url"],
                                "metrics": _row_metrics(existing),
                            },
                            *prior_appeared,
                        ],
                        owner_source=item.source,
                        owner_url=item.canonical_url,
                    )
                    result.new_items.append(item)
                    seen_ids_in_batch[item_id] = item
                    seen_title_hashes_in_batch[title_hash] = item
                    if is_stale:
                        result.resurface_ids.append(existing["id"])
                else:
                    appeared, changed = _merge_appearances(
                        prior_appeared,
                        [{
                            "source": item.source,
                            "source_handle": item.source_handle,
                            "url": item.url,
                            "metrics": _metrics_summary(item.engagement),
                        }],
                        owner_source=existing["source"],
                        owner_url=existing["canonical_url"],
                    )
                    if changed and not self.dry_run:
                        self.db.update_also_appeared_in(existing["id"], json.dumps(appeared))
                    if is_stale:
                        result.resurface_ids.append(existing["id"])
                continue

            if can_l2 and title_hash in seen_title_hashes_in_batch:
                prior = seen_title_hashes_in_batch[title_hash]
                if item.source_tier < prior.source_tier:
                    result.new_items.remove(prior)
                    item.also_appeared_in, _ = _merge_appearances(
                        item.also_appeared_in,
                        [
                            {
                                "source": prior.source,
                                "source_handle": prior.source_handle,
                                "url": prior.url,
                                "metrics": _metrics_summary(prior.engagement),
                            },
                            *prior.also_appeared_in,
                        ],
                        owner_source=item.source,
                        owner_url=item.canonical_url,
                    )
                    result.new_items.append(item)
                    seen_title_hashes_in_batch[title_hash] = item
                    seen_ids_in_batch[item_id] = item
                else:
                    prior.also_appeared_in, _ = _merge_appearances(
                        prior.also_appeared_in,
                        [{
                            "source": item.source,
                            "source_handle": item.source_handle,
                            "url": item.url,
                            "metrics": _metrics_summary(item.engagement),
                        }],
                        owner_source=prior.source,
                        owner_url=prior.canonical_url,
                    )
                continue

            result.new_items.append(item)
            seen_ids_in_batch[item_id] = item
            if can_l2:
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
