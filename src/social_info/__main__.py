"""CLI entrypoint: `uv run python -m social_info [--flags]`."""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from social_info.config import load_config
from social_info.db import Database
from social_info.markdown import render_item
from social_info.pipeline import run_pipeline, write_report

TAIPEI = timezone(timedelta(hours=8))


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
    return p.parse_args()


async def _main() -> int:
    args = _parse_args()
    config = load_config(args.config)
    db = Database(args.db)
    db.init_schema()

    only_sources = [s.strip() for s in args.source.split(",")] if args.source else None
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
    out = write_report(new_items, failures, args.reports, date, now_tw)
    print(f"Wrote {out} ({len(new_items)} items, {len(failures)} failures)")
    db.close()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
