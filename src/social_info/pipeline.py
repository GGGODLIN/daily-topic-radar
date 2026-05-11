"""End-to-end orchestration: load config -> fetch all -> dedup -> render."""
import asyncio
import re
from datetime import datetime
from pathlib import Path

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
) -> tuple[list[Item], list[FetchResult]]:
    """Run all enabled fetchers in parallel, dedup, return (new_items, all_results)."""
    enabled = config.enabled_sources()
    if only_sources:
        enabled = [s for s in enabled if s.id in only_sources]

    async with httpx.AsyncClient(follow_redirects=True, timeout=HTTP_TIMEOUT) as http:
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
                error_class=r.error_class,
                attempts=r.attempts,
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
