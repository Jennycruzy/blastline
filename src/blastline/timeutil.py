"""Strict UTC time helpers."""

from __future__ import annotations

from datetime import datetime, timezone


UTC = timezone.utc


def parse_time(value: str, context: str = "timestamp") -> datetime:
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"{context} is not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{context} must include a timezone: {value!r}")
    return parsed.astimezone(UTC)


def format_time(value: datetime) -> str:
    normalized = value.astimezone(UTC).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def now_utc() -> datetime:
    return datetime.now(UTC)
