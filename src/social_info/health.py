"""Print a 7-day per-source success-rate report.

Run: `uv run python -m social_info.health`
"""
import argparse
from collections import defaultdict
from pathlib import Path

from social_info.db import Database


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, default=Path("state.db"))
    p.add_argument("--days", type=int, default=7)
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    db = Database(args.db)
    db.init_schema()

    runs = db.recent_fetch_runs(days=args.days)
    by_source: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        by_source[r["source"]].append(r)

    print(f"=== Health report (last {args.days} days) ===")
    print(
        f"{'source':<30} {'runs':>5} {'ok':>5} {'fail':>5} {'rate':>7} {'last_error'}"
    )
    for source, rs in sorted(by_source.items()):
        ok = sum(1 for r in rs if r["status"] == "ok")
        fail = sum(1 for r in rs if r["status"] != "ok")
        rate = ok / len(rs) * 100 if rs else 0
        last_err = next((r["error"] for r in rs if r["status"] != "ok"), "")
        print(
            f"{source:<30} {len(rs):>5} {ok:>5} {fail:>5} {rate:>6.1f}% {last_err[:60]}"
        )

    db.close()


if __name__ == "__main__":
    main()
