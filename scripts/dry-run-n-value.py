"""Dry-run: simulate applying different N-day resurface thresholds
to see how many extra items would surface per day."""
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path("state.db")
N_VALUES = [15, 30, 60]
_UTC = timezone.utc  # noqa: UP017 — keep compatible with macOS /usr/bin/python3 (3.9)


def main():
    if not DB_PATH.is_file():
        print(f"Error: database not found: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    now = datetime.now(_UTC).replace(tzinfo=None)
    since = (now - timedelta(days=30)).isoformat()
    all_fetched_gt = conn.execute(
        "SELECT id, url, title, source, posted_at, last_surfaced_at "
        "FROM items WHERE fetched_at > ?",
        (since,),
    ).fetchall()

    print(f"Items with fetched_at within past 30d: {len(all_fetched_gt)}")

    for n in N_VALUES:
        threshold = now - timedelta(days=n)
        would_resurface = 0
        for row in all_fetched_gt:
            lsa_str = row["last_surfaced_at"] or row["posted_at"]
            if not lsa_str:
                continue
            try:
                lsa = datetime.fromisoformat(lsa_str.replace("Z", "+00:00"))
                if lsa.tzinfo is not None:
                    lsa = lsa.astimezone(_UTC).replace(tzinfo=None)
            except (ValueError, TypeError):
                continue
            if lsa < threshold:
                would_resurface += 1
        print(f"  N={n}: {would_resurface} items would resurface / 30 days "
              f"(~{would_resurface/30:.1f} / day)")

    conn.close()


if __name__ == "__main__":
    main()
