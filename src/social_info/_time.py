"""Naive UTC datetime helpers (replacements for deprecated datetime.utcnow)."""
import calendar
import math
from datetime import UTC, datetime
from typing import Any, overload


def utcnow() -> datetime:
    """Current UTC time as a naive datetime (drop-in for datetime.utcnow)."""
    return datetime.now(UTC).replace(tzinfo=None)


def utcfromtimestamp(ts: float) -> datetime:
    """Naive UTC datetime from a Unix timestamp (drop-in for datetime.utcfromtimestamp)."""
    return datetime.fromtimestamp(ts, UTC).replace(tzinfo=None)


@overload
def parse_iso_utc(value: Any, fallback: datetime) -> datetime: ...


@overload
def parse_iso_utc(value: Any, fallback: None) -> datetime | None: ...


def parse_iso_utc(value: Any, fallback: datetime | None) -> datetime | None:
    """Parse an ISO-8601 timestamp into naive UTC, returning fallback on error."""
    if not isinstance(value, str):
        return fallback
    cleaned = value.strip()
    if not cleaned:
        return fallback
    if cleaned.endswith(("Z", "z")):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(UTC).replace(tzinfo=None)
        return parsed
    except (TypeError, ValueError, OverflowError):
        return fallback


@overload
def safe_utcfromtimestamp(ts: Any, fallback: datetime) -> datetime: ...


@overload
def safe_utcfromtimestamp(ts: Any, fallback: None) -> datetime | None: ...


def safe_utcfromtimestamp(ts: Any, fallback: datetime | None) -> datetime | None:
    """Convert Unix seconds to naive UTC, returning fallback on bad/out-of-range inputs."""
    if ts is None or isinstance(ts, bool):
        return fallback
    try:
        val = float(ts)
    except (TypeError, ValueError, OverflowError):
        return fallback
    if not math.isfinite(val):
        return fallback
    try:
        return utcfromtimestamp(val)
    except (OverflowError, OSError, ValueError):
        return fallback


@overload
def struct_time_to_utc(parsed: Any, fallback: datetime) -> datetime: ...


@overload
def struct_time_to_utc(parsed: Any, fallback: None) -> datetime | None: ...


def struct_time_to_utc(parsed: Any, fallback: datetime | None) -> datetime | None:
    """Convert a UTC struct_time/9-tuple to naive UTC without local-TZ mktime bias."""
    if parsed is None:
        return fallback
    try:
        ts = calendar.timegm(parsed)
    except (TypeError, ValueError, IndexError, OverflowError):
        return fallback
    return safe_utcfromtimestamp(ts, fallback)

