"""Timezone handling and schedule slot generation for LOOPSTASH."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def get_timezone(tz_name: Optional[str]) -> ZoneInfo:
    """Return a validated ZoneInfo object. Defaults to UTC if None or empty."""
    if not tz_name or not tz_name.strip():
        return ZoneInfo("UTC")
    clean_tz = tz_name.strip()
    try:
        return ZoneInfo(clean_tz)
    except (ZoneInfoNotFoundError, KeyError) as e:
        raise ValueError(
            f"Invalid timezone '{clean_tz}'. Please use a standard IANA timezone name "
            "(e.g. 'UTC', 'Asia/Kolkata', 'America/New_York')."
        ) from e


def parse_schedule_datetime(
    date_str: str,
    time_str: str,
    tz_name: Optional[str] = "UTC",
) -> datetime:
    """Parse date (YYYY-MM-DD) and time (HH:MM or HH:MM:SS) in given timezone.

    Returns a timezone-aware datetime object.
    """
    tz = get_timezone(tz_name)

    clean_date = date_str.strip()
    clean_time = time_str.strip()

    try:
        parsed_date = date.fromisoformat(clean_date)
    except ValueError as e:
        raise ValueError(f"Invalid date format '{date_str}'. Expected YYYY-MM-DD.") from e

    # Parse time
    time_parts = clean_time.split(":")
    if len(time_parts) not in (2, 3):
        raise ValueError(f"Invalid time format '{time_str}'. Expected HH:MM or HH:MM:SS.")

    try:
        hour = int(time_parts[0])
        minute = int(time_parts[1])
        second = int(time_parts[2]) if len(time_parts) == 3 else 0
        parsed_time = time(hour=hour, minute=minute, second=second)
    except ValueError as e:
        raise ValueError(f"Invalid time values in '{time_str}': {e}") from e

    naive_dt = datetime.combine(parsed_date, parsed_time)
    # Localize with ZoneInfo
    localized_dt = naive_dt.replace(tzinfo=tz)
    return localized_dt


def to_utc_iso(dt: datetime) -> str:
    """Convert aware datetime to YouTube-compatible UTC ISO8601 (YYYY-MM-DDTHH:MM:SSZ)."""
    if dt.tzinfo is None:
        raise ValueError("Cannot convert naive datetime to UTC ISO format without timezone.")
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_future_datetime(
    dt: datetime, min_lead_seconds: int = 60
) -> tuple[bool, Optional[str]]:
    """Validate that dt is in the future by at least min_lead_seconds.

    Returns (is_valid, error_message).
    """
    now_utc = datetime.now(timezone.utc)
    target_utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    diff = (target_utc - now_utc).total_seconds()
    if diff <= 0:
        return (
            False,
            f"Scheduled time ({target_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}) is in the past.",
        )
    if diff < min_lead_seconds:
        return (
            False,
            f"Scheduled time must be at least {min_lead_seconds} seconds in the future.",
        )
    return True, None


def parse_interval(interval_spec: str | int) -> timedelta:
    """Parse an interval specification like 'daily', 'every_2d', 'weekly', or integer days."""
    if isinstance(interval_spec, int):
        if interval_spec <= 0:
            raise ValueError("Interval days must be greater than 0.")
        return timedelta(days=interval_spec)

    spec = str(interval_spec).strip().lower()
    if spec in ("daily", "1d", "1", "day", "every day", "everyday"):
        return timedelta(days=1)
    if spec in ("every_2d", "2d", "2", "every 2 days", "every other day"):
        return timedelta(days=2)
    if spec in ("every_3d", "3d", "3", "every 3 days"):
        return timedelta(days=3)
    if spec in ("weekly", "7d", "7", "week", "every week"):
        return timedelta(days=7)
    if spec.endswith("d") and spec[:-1].isdigit():
        days = int(spec[:-1])
        if days <= 0:
            raise ValueError("Interval days must be greater than 0.")
        return timedelta(days=days)
    if spec.endswith("h") and spec[:-1].isdigit():
        hours = int(spec[:-1])
        if hours <= 0:
            raise ValueError("Interval hours must be greater than 0.")
        return timedelta(hours=hours)

    try:
        days = int(spec)
        if days <= 0:
            raise ValueError("Interval days must be greater than 0.")
        return timedelta(days=days)
    except ValueError as e:
        raise ValueError(
            f"Unknown interval specification '{interval_spec}'. "
            "Supported: 'daily', 'every_2d', 'weekly', 'Nd' (e.g. '3d'), or 'Nh' (e.g. '12h')."
        ) from e


def generate_schedule_slots(
    start_date: str,
    start_time: str,
    tz_name: str,
    count: int,
    interval: str | int = "daily",
    min_lead_seconds: int = 60,
) -> list[dict[str, Any]]:
    """Generate a series of publication slots.

    Each slot dictionary contains:
    - index: 1-based slot number
    - local_datetime: aware datetime in display timezone
    - local_display: formatted string 'YYYY-MM-DD HH:MM:SS (TZ)'
    - utc_datetime: aware datetime in UTC
    - utc_iso: ISO 8601 string with 'Z' suffix
    - timezone: timezone name string
    """
    if count <= 0:
        raise ValueError("Schedule slot count must be at least 1.")

    tz = get_timezone(tz_name)
    first_dt = parse_schedule_datetime(start_date, start_time, tz_name)

    is_valid, err = validate_future_datetime(first_dt, min_lead_seconds=min_lead_seconds)
    if not is_valid:
        raise ValueError(f"Starting schedule slot is invalid: {err}")

    step = parse_interval(interval)
    slots = []

    current_dt = first_dt
    for i in range(1, count + 1):
        utc_dt = current_dt.astimezone(timezone.utc)
        tz_label = tz.key if hasattr(tz, "key") else tz_name
        formatted_display = f"{current_dt.strftime('%Y-%m-%d %H:%M:%S')} ({tz_label})"
        slots.append(
            {
                "index": i,
                "local_datetime": current_dt,
                "local_display": formatted_display,
                "utc_datetime": utc_dt,
                "utc_iso": to_utc_iso(utc_dt),
                "timezone": getattr(tz, "key", tz_name),
            }
        )
        # Advance by interval; handle daylight saving / wall clock shift gracefully
        # Adding timedelta to localized datetime in Python 3.9+ ZoneInfo automatically handles DST
        current_dt = current_dt + step

    return slots
