"""CLI entrypoint: `uv run python -m social_info [--flags]`."""
import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from social_info.config import load_config  # noqa: E402
from social_info.db import Database  # noqa: E402
from social_info.fetchers.base import Item  # noqa: E402
from social_info.markdown import render_item  # noqa: E402
from social_info.pipeline import run_pipeline, write_report  # noqa: E402

TAIPEI = timezone(timedelta(hours=8))


def _row_to_item(row: dict) -> Item:
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
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Daily AI raw aggregator")
    p.add_argument("--config", type=Path, default=Path("sources.yml"))
    p.add_argument("--db", type=Path, default=Path("state.db"))
    p.add_argument("--reports", type=Path, default=Path("reports"))
    p.add_argument(
        "--source",
        type=str,
        default=None,
        help="comma-separated source ids to run (default: all enabled)",
    )
    p.add_argument(
        "--date",
        type=str,
        default=None,
        help="YYYY-MM-DD (Asia/Taipei) for output filename; default = today",
    )
    p.add_argument("--dry-run", action="store_true", help="don't write db / .md")
    p.add_argument(
        "--smoke",
        action="store_true",
        help="real API + limit=3 per source + print to stdout, no write",
    )
    p.add_argument(
        "--retry-failures",
        action="store_true",
        help="re-run only sources whose most recent fetch_run was a failure",
    )
    return p.parse_args()


async def _main() -> int:
    args = _parse_args()
    config = load_config(args.config)
    db = Database(args.db)
    db.init_schema()

    only_sources = [s.strip() for s in args.source.split(",")] if args.source else None
    if args.retry_failures:
        retry_targets = db.last_failed_sources()
        if not retry_targets:
            print("No sources to retry — every source's last run was successful.")
            db.close()
            return 0
        only_sources = retry_targets
        print(f"Retrying {len(retry_targets)} failed source(s): {', '.join(retry_targets)}")
    limit_per_source = 3 if args.smoke else None
    dry_run = args.dry_run or args.smoke

    new_items, results = await run_pipeline(
        config,
        db,
        only_sources=only_sources,
        dry_run=dry_run,
        limit_per_source=limit_per_source,
    )
    failures = [r for r in results if not r.ok]

    if args.smoke:
        print("--- SMOKE RUN ---")
        print(
            f"Sources requested: {len(results)}, "
            f"succeeded: {len(results) - len(failures)}"
        )
        for it in new_items[:30]:
            print(render_item(it))
        for f in failures:
            print(f"FAILED: {f.source_id}: {f.error}")
        db.close()
        return 0

    if dry_run:
        print(f"DRY-RUN: {len(new_items)} new items, {len(failures)} failures")
        db.close()
        return 0

    now_tw = datetime.now(TAIPEI)
    date = args.date or now_tw.strftime("%Y-%m-%d")
    rows = db.items_for_date(date)
    all_items_today = [_row_to_item(r) for r in rows]
    out = write_report(all_items_today, failures, args.reports, date, now_tw)
    print(
        f"Wrote {out} ({len(all_items_today)} items rendered, "
        f"{len(new_items)} new this run, {len(failures)} failures)"
    )
    db.close()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
