"""End-to-end orchestration: load config -> fetch all -> dedup -> render."""
import asyncio
from datetime import datetime
from pathlib import Path

import httpx

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
    twitter,
    wewe_rss,
)
from social_info.fetchers.base import FetchResult, Item
from social_info.markdown import render_file

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
    "wewe_rss": wewe_rss.fetch,
}


async def _run_one_fetcher(source: SourceConfig, http: httpx.AsyncClient) -> FetchResult:
    started = datetime.utcnow()
    fetcher = FETCHER_REGISTRY.get(source.type)
    if not fetcher:
        return FetchResult(
            source_id=source.id,
            ok=False,
            error=f"unknown source type: {source.type}",
            started_at=started,
            ended_at=datetime.utcnow(),
        )
    try:
        items = await fetcher(source, http)
        return FetchResult(
            source_id=source.id,
            items=items,
            ok=True,
            started_at=started,
            ended_at=datetime.utcnow(),
        )
    except Exception as e:
        return FetchResult(
            source_id=source.id,
            items=[],
            ok=False,
            error=f"{type(e).__name__}: {e}",
            started_at=started,
            ended_at=datetime.utcnow(),
        )


async def run_pipeline(
    config: Config,
    db: Database,
    *,
    only_sources: list[str] | None = None,
    dry_run: bool = False,
    limit_per_source: int | None = None,
) -> tuple[list[Item], list[FetchResult]]:
    """Run all enabled fetchers in parallel, dedup, return (new_items, all_results)."""
    enabled = config.enabled_sources()
    if only_sources:
        enabled = [s for s in enabled if s.id in only_sources]

    async with httpx.AsyncClient(follow_redirects=True) as http:
        results = await asyncio.gather(*[_run_one_fetcher(s, http) for s in enabled])

    if not dry_run:
        for r in results:
            db.log_fetch_run(
                source=r.source_id,
                started_at=r.started_at,
                ended_at=r.ended_at or r.started_at,
                status="ok" if r.ok else "failed",
                items_fetched=r.items_count(),
                error=r.error,
            )

    all_items: list[Item] = []
    for r in results:
        if not r.ok:
            continue
        items = r.items[:limit_per_source] if limit_per_source else r.items
        all_items.extend(items)

    deduper = Deduper(db)
    new_items = deduper.process(all_items)

    if not dry_run:
        for it in new_items:
            row = it.to_db_row(
                item_id=compute_item_id(it.canonical_url),
                title_hash=compute_title_hash(it.title),
            )
            db.insert_item(row)

    return new_items, results


def write_report(
    new_items: list[Item],
    failures: list[FetchResult],
    out_dir: Path,
    date: str,
    generated_at: datetime,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render_file(
        date=date, generated_at=generated_at, items=new_items, failures=failures
    )
    out_path = out_dir / f"{date}.md"
    out_path.write_text(md, encoding="utf-8")
    return out_path
