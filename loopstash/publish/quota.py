"""YouTube API quota tracking, preflight verification, and rate-limit guardrails."""

from __future__ import annotations

from datetime import datetime, timezone

from loopstash.database import Database
from loopstash.models import QuotaRecord

DEFAULT_DAILY_UPLOAD_LIMIT = 100


class QuotaManager:
    """Monitors daily YouTube API usage, upload units, and enforces preflight limits."""

    def __init__(self, db: Database, default_limit: int | None = None):
        self.db = db
        self.custom_limit = default_limit

    def get_today_record(self) -> QuotaRecord:
        """Fetch today's quota usage record in UTC."""
        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.db.get_today_quota(today_utc)

    def get_effective_limit(self, rec: QuotaRecord | None = None) -> int:
        """Return the effective daily limit considering custom override and DB record."""
        if self.custom_limit is not None:
            return self.custom_limit
        if rec is None:
            rec = self.get_today_record()
        return rec.daily_limit if rec.daily_limit > 0 else DEFAULT_DAILY_UPLOAD_LIMIT

    def check_upload_quota(self, units_needed: int = 1) -> tuple[bool, int, int]:
        """Verify if units_needed can be accommodated within today's daily upload limit.

        Returns: (is_allowed, units_used, daily_limit)
        """
        rec = self.get_today_record()
        limit = self.get_effective_limit(rec)
        is_allowed = (rec.upload_units_used + units_needed) <= limit
        return is_allowed, rec.upload_units_used, limit

    def record_upload(self, units: int = 1, api_requests: int = 1) -> QuotaRecord:
        """Record consumed upload units and API calls."""
        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.db.record_quota_usage(
            upload_units=units,
            api_requests=api_requests,
            date_utc=today_utc,
        )

    def remaining_upload_capacity(self) -> int:
        """Calculate how many video uploads remain today before hitting quota."""
        rec = self.get_today_record()
        limit = self.get_effective_limit(rec)
        return max(0, limit - rec.upload_units_used)

    def format_quota_status(self) -> dict[str, str | int]:
        """Return formatted summary for diagnostics and CLI output."""
        rec = self.get_today_record()
        limit = self.get_effective_limit(rec)
        remaining = max(0, limit - rec.upload_units_used)
        return {
            "date_utc": rec.date_utc,
            "uploads_used": rec.upload_units_used,
            "daily_limit": limit,
            "remaining_capacity": remaining,
            "api_requests": rec.api_requests_count,
            "last_request": rec.last_request_time or "None",
        }
