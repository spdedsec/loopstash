"""Tests for YouTube API quota tracking, rate limits, and preflight guardrails."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from loopstash.database import Database
from loopstash.publish.quota import QuotaManager


@pytest.fixture
def quota_db(tmp_path: Path) -> Database:
    db_file = tmp_path / "test_quota.db"
    return Database(db_file)


def test_quota_manager_defaults(quota_db: Database):
    """Test initial daily quota values and capacity."""
    qm = QuotaManager(quota_db)
    rec = qm.get_today_record()

    assert rec.upload_units_used == 0
    assert rec.api_requests_count == 0
    assert rec.daily_limit == 100
    assert qm.remaining_upload_capacity() == 100

    allowed, used, limit = qm.check_upload_quota(units_needed=1)
    assert allowed is True
    assert used == 0
    assert limit == 100


def test_quota_record_upload(quota_db: Database):
    """Test recording consumption of quota units and api requests."""
    qm = QuotaManager(quota_db, default_limit=10)

    # Consume 3 uploads and 5 API calls
    rec = qm.record_upload(units=3, api_requests=5)
    assert rec.upload_units_used == 3
    assert rec.api_requests_count == 5
    assert qm.remaining_upload_capacity() == 7

    # Check capacity with units_needed
    allowed, used, limit = qm.check_upload_quota(units_needed=7)
    assert allowed is True
    assert used == 3
    assert limit == 10

    # 8 units would exceed 10
    allowed, used, limit = qm.check_upload_quota(units_needed=8)
    assert allowed is False


def test_quota_capacity_exhaustion(quota_db: Database):
    """Test exact limit boundary and zero capacity calculation."""
    qm = QuotaManager(quota_db, default_limit=4)
    qm.record_upload(units=4, api_requests=4)

    assert qm.remaining_upload_capacity() == 0
    allowed, used, limit = qm.check_upload_quota(units_needed=1)
    assert allowed is False
    assert used == 4
    assert limit == 4


def test_quota_format_summary(quota_db: Database):
    """Test diagnostic formatting for CLI and doctor command."""
    qm = QuotaManager(quota_db, default_limit=25)
    qm.record_upload(units=5, api_requests=12)

    status = qm.format_quota_status()
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    assert status["date_utc"] == today_utc
    assert status["uploads_used"] == 5
    assert status["daily_limit"] == 25
    assert status["remaining_capacity"] == 20
    assert status["api_requests"] == 12
