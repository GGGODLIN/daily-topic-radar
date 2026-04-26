"""Naive UTC datetime helpers (replacements for deprecated datetime.utcnow)."""
from datetime import UTC, datetime


def utcnow() -> datetime:
    """Current UTC time as a naive datetime (drop-in for datetime.utcnow)."""
    return datetime.now(UTC).replace(tzinfo=None)


def utcfromtimestamp(ts: float) -> datetime:
    """Naive UTC datetime from a Unix timestamp (drop-in for datetime.utcfromtimestamp)."""
    return datetime.fromtimestamp(ts, UTC).replace(tzinfo=None)
