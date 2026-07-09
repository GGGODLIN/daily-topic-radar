"""End-to-end orchestration: load config -> fetch all -> dedup -> render."""
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from social_info._time import utcnow
from social_info.config import Config, SourceConfig
from social_info.db import Database
from social_info.dedup import Deduper, compute_item_id, compute_title_hash
from social_info.fetchers import (
    github_trending,
    hn,
    huggingface,
    product_hunt,
    reddit,
    rss,
    rsshub,
    threads,
    threads_apify,
    trendshift,
    twitter,
    v2ex,
    wewe_rss,
)
from social_info.fetchers.base import FetchResult, Item
from social_info.markdown import render_file

_SECRET_QS_RE = re.compile(
    r"([?&](?:token|apikey|api_key|access_token|auth|key)=)[^&\s'\"]+",
    re.IGNORECASE,
)


def _redact_secrets(s: str) -> str:
    return _SECRET_QS_RE.sub(r"\1***", s)


TRANSIENT_RETRY_DELAYS = (5, 15, 30)
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def classify_error(exc: BaseException) -> str:
    """Classify a fetch exception into one of:
    - transient: network glitch / 5xx / DNS race; retry-able
    - user_action_required: 401/403; auth/VPN problem only the user can resolve
    - persistent_error: 4xx other than 401/403, unknown source type, etc.
    """
    if isinstance(
        exc,
        (
            httpx.ReadTimeout,
            httpx.ConnectTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            httpx.ReadError,
            httpx.ConnectError,
            httpx.WriteError,
            httpx.RemoteProtocolError,
            httpx.NetworkError,
        ),
    ):
        return "transient"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code >= 500:
            return "transient"
        if code in (401, 403):
            return "user_action_required"
        return "persistent_error"
    return "persistent_error"


def annotate_net_new(
    results: list["FetchResult"], new_items: list["Item"]
) -> None:
    """Set FetchResult.net_new = how many of a source's fetched items survived
    dedup into the report. Ok results that fetched items but contributed 0
    net-new (all duplicates) get net_new=0 — distinguishable from a failed
    fetch. Failed results keep net_new=None.
    """
    survivors = {id(it) for it in new_items}
    for r in results:
        if not r.ok:
            continue
        r.net_new = sum(1 for it in r.items if id(it) in survivors)


FETCHER_REGISTRY = {
    "hn_algolia": hn.fetch,
    "reddit": reddit.fetch,
    "github_trending": github_trending.fetch,
    "product_hunt": product_hunt.fetch,
    "huggingface": huggingface.fetch,
    "rss": rss.fetch,
    "rsshub": rsshub.fetch,
    "twitter": twitter.fetch,
    "threads": threads.fetch,
    "threads_apify": threads_apify.fetch,
    "trendshift": trendshift.fetch,
    "v2ex": v2ex.fetch,
    "wewe_rss": wewe_rss.fetch,
}


async def _run_one_fetcher(source: SourceConfig, http: httpx.AsyncClient) -> FetchResult:
    started = utcnow()
    fetcher = FETCHER_REGISTRY.get(source.type)
    if not fetcher:
        return FetchResult(
            source_id=source.id,
            ok=False,
            error=f"unknown source type: {source.type}",
            error_class="persistent_error",
            attempts=0,
            started_at=started,
            ended_at=utcnow(),
        )

    last_exc: BaseException | None = None
    last_class: str = ""
    attempt = 0
    for delay_index in range(len(TRANSIENT_RETRY_DELAYS) + 1):
        attempt += 1
        try:
            items = await fetcher(source, http)
            return FetchResult(
                source_id=source.id,
                items=items,
                ok=True,
                attempts=attempt,
                started_at=started,
                ended_at=utcnow(),
            )
        except Exception as e:
            last_exc = e
            last_class = classify_error(e)
            if last_class != "transient":
                break
            if delay_index < len(TRANSIENT_RETRY_DELAYS):
                await asyncio.sleep(TRANSIENT_RETRY_DELAYS[delay_index])
                continue
            break

    return FetchResult(
        source_id=source.id,
        items=[],
        ok=False,
        error=_redact_secrets(f"{type(last_exc).__name__}: {last_exc}"),
        error_class=last_class or "persistent_error",
        attempts=attempt,
        started_at=started,
        ended_at=utcnow(),
    )


async def run_pipeline(
    config: Config,
    db: Database,
    *,
    only_sources: list[str] | None = None,
    dry_run: bool = False,
    limit_per_source: int | None = None,
    resurface_days: int = 30,
) -> tuple[list[Item], list[FetchResult]]:
    """Run all enabled fetchers in parallel, dedup, return (new_items, all_results).

    resurface_ids computed by the dedup pass are not resolved or applied here —
    see resolve_resurface_items() / db.update_last_surfaced_at(). Timestamps
    must only be bumped once a caller has actually rendered those items, so
    it stays out of this function's unconditional per-run side effects.
    """
    enabled = config.enabled_sources()
    if only_sources:
        enabled = [s for s in enabled if s.id in only_sources]

    async with httpx.AsyncClient(follow_redirects=True, timeout=HTTP_TIMEOUT) as http:
        results = await asyncio.gather(*[_run_one_fetcher(s, http) for s in enabled])

    all_items: list[Item] = []
    for r in results:
        if not r.ok:
            continue
        items = r.items[:limit_per_source] if limit_per_source else r.items
        all_items.extend(items)

    deduper = Deduper(db, resurface_days=resurface_days)
    dedup_result = deduper.process(all_items)
    new_items = dedup_result.new_items
    annotate_net_new(results, new_items)

    if not dry_run:
        for r in results:
            db.log_fetch_run(
                source=r.source_id,
                started_at=r.started_at,
                ended_at=r.ended_at or r.started_at,
                status="ok" if r.ok else "failed",
                items_fetched=r.items_count(),
                error=r.error,
                error_class=r.error_class,
                attempts=r.attempts,
                net_new=r.net_new,
            )
        for it in new_items:
            row = it.to_db_row(
                item_id=compute_item_id(it.canonical_url),
                title_hash=compute_title_hash(it.title),
            )
            row["last_surfaced_at"] = it.fetched_at.isoformat()
            db.insert_item(row)

    return new_items, results


def _row_to_item(row: dict[str, Any]) -> Item:
    return Item(
        title=row["title"],
        url=row["url"],
        canonical_url=row["canonical_url"],
        source=row["source"],
        source_handle=row["source_handle"] or "",
        source_tier=row["source_tier"] or 2,
        posted_at=datetime.fromisoformat(row["posted_at"]),
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
        author=row.get("author") or "",
        excerpt=row.get("excerpt") or "",
        language=row.get("language") or "en",
        engagement=json.loads(row.get("engagement_json") or "{}"),
        also_appeared_in=json.loads(row.get("also_appeared_in") or "[]"),
        comments=json.loads(row.get("comments_json") or "[]"),
    )


def resolve_resurface_items(db: Database, resurface_ids: list[str]) -> list[Item]:
    """Rebuild full Item objects for resurface_ids so they can be rendered.

    Does not touch last_surfaced_at — call db.update_last_surfaced_at()
    separately, after the caller has actually rendered the returned items.
    """
    rows = db.get_items_by_ids(resurface_ids)
    return [_row_to_item(r) for r in rows]


def write_report(
    new_items: list[Item],
    failures: list[FetchResult],
    out_dir: Path,
    date: str,
    generated_at: datetime,
    stale: list[FetchResult] | None = None,
    resurface_items: list[Item] | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render_file(
        date=date,
        generated_at=generated_at,
        items=new_items,
        failures=failures,
        stale=stale,
        resurface_items=resurface_items,
    )
    out_path = out_dir / f"{date}.md"
    out_path.write_text(md, encoding="utf-8")
    return out_path
