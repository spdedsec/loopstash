"""Tests for YouTube publication calendar generation, timezone conversion, and scheduling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from loopstash.database import Database
from loopstash.models import (
    MediaItem,
    PublishingAccount,
    PublishingJob,
    PublishingMetadata,
    PublishStatus,
)
from loopstash.publish.scheduler import YouTubeScheduler
from loopstash.utils.hashing import compute_file_sha256
from loopstash.utils.timezones import (
    generate_schedule_slots,
    parse_interval,
    parse_schedule_datetime,
    to_utc_iso,
    validate_future_datetime,
)


@pytest.fixture
def sched_db(tmp_path: Path) -> Database:
    db_file = tmp_path / "test_scheduling.db"
    return Database(db_file)


def test_parse_interval():
    """Verify standard and shorthand interval parsing."""
    assert parse_interval("daily") == timedelta(days=1)
    assert parse_interval("every_2d") == timedelta(days=2)
    assert parse_interval("weekly") == timedelta(days=7)
    assert parse_interval("3d") == timedelta(days=3)
    assert parse_interval("6h") == timedelta(hours=6)
    assert parse_interval(12) == timedelta(days=12)

    with pytest.raises(ValueError):
        parse_interval("invalid_interval_spec")


def test_generate_schedule_slots_intervals():
    """Verify slot generation across multiple intervals and timezones."""
    # Use future date
    future_date = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%d")

    # Daily slots
    slots = generate_schedule_slots(
        start_date=future_date,
        start_time="14:00",
        tz_name="America/New_York",
        count=3,
        interval="daily",
    )
    assert len(slots) == 3
    assert slots[0]["index"] == 1
    assert slots[1]["index"] == 2
    assert slots[2]["index"] == 3
    # Check that UTC ISO has trailing Z
    assert slots[0]["utc_iso"].endswith("Z")
    assert slots[1]["utc_iso"].endswith("Z")


def test_generate_large_schedule_slots_performance():
    """Verify performance and ordering when generating 100+ calendar slots."""
    future_date = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")
    slots = generate_schedule_slots(
        start_date=future_date,
        start_time="09:00",
        tz_name="UTC",
        count=150,
        interval="daily",
    )
    assert len(slots) == 150
    # First and last slot timestamps should differ by 149 days
    dt_first = datetime.fromisoformat(slots[0]["utc_iso"].replace("Z", "+00:00"))
    dt_last = datetime.fromisoformat(slots[-1]["utc_iso"].replace("Z", "+00:00"))
    assert dt_last - dt_first == timedelta(days=149)


def test_past_date_rejection_and_future_validation():
    """Test validation of past, immediate, and future schedule dates."""
    # Past date
    past_dt = datetime.now(timezone.utc) - timedelta(days=1)
    valid, err = validate_future_datetime(past_dt)
    assert valid is False
    assert err is not None and "past" in err.lower()

    # Immediate (under 60 seconds)
    near_dt = datetime.now(timezone.utc) + timedelta(seconds=20)
    valid, err = validate_future_datetime(near_dt, min_lead_seconds=60)
    assert valid is False
    assert err is not None and "at least 60 seconds" in err.lower()

    # Valid future date
    ok_dt = datetime.now(timezone.utc) + timedelta(hours=2)
    valid, err = validate_future_datetime(ok_dt)
    assert valid is True
    assert err is None


def test_timezone_conversion_to_utc_iso():
    """Test precise timezone parsing and conversion to UTC ISO8601."""
    # America/New_York is UTC-5 (or UTC-4 in EDT)
    dt = parse_schedule_datetime("2026-12-01", "12:00", "America/New_York")
    utc_iso = to_utc_iso(dt)
    assert utc_iso == "2026-12-01T17:00:00Z"

    # Asia/Tokyo is UTC+9
    dt_tokyo = parse_schedule_datetime("2026-12-01", "18:00", "Asia/Tokyo")
    utc_iso_tokyo = to_utc_iso(dt_tokyo)
    assert utc_iso_tokyo == "2026-12-01T09:00:00Z"


def test_schedule_batch_execution(sched_db: Database, tmp_path: Path):
    """Test scheduling media batch with rights verification and private visibility."""
    account = PublishingAccount(
        id="UC_SCHED_01",
        channel_id="UC_SCHED_01",
        channel_title="Scheduler Channel",
        token_storage_key="file:UC_SCHED_01",
    )
    sched_db.upsert_publishing_account(account)

    video_file = tmp_path / "sched_video.mp4"
    video_file.write_bytes(b"SCHEDULER_VIDEO_CONTENT")
    sha = compute_file_sha256(video_file)

    media = MediaItem(
        id="med_sched_01",
        file_path=str(video_file),
        file_size=video_file.stat().st_size,
        sha256=sha,
        title="Scheduled Reel 1",
    )
    sched_db.upsert_media_item(media)

    meta = PublishingMetadata(
        media_id="med_sched_01",
        title="Scheduled Reel 1",
        description="Scheduled Description",
        privacy_status="private",
        rights_confirmed=False,
    )
    sched_db.upsert_publishing_metadata(meta)

    future_date = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")

    mock_uploader = MagicMock()
    mock_auth = MagicMock()
    mock_service = MagicMock()
    mock_auth.build_service.return_value = mock_service

    scheduler = YouTubeScheduler(
        db=sched_db,
        uploader=mock_uploader,
        auth_manager=mock_auth,
    )

    # 1. Attempt without confirm_rights -> rejected by Section 13 safeguards
    results = scheduler.schedule_batch(
        media_ids=["med_sched_01"],
        channel_id="UC_SCHED_01",
        start_date=future_date,
        start_time="10:00",
        timezone_str="UTC",
        confirm_rights=False,
    )
    assert len(results) == 1
    assert results[0]["status"] == "NEEDS_ATTENTION"
    assert "Publishing rights not confirmed" in results[0]["error"]
    assert not mock_uploader.upload_media.called

    # 2. Confirm rights -> should trigger upload with scheduled publishAt
    mock_uploader.upload_media.return_value = (
        "yt_sched_vid_01",
        "https://youtu.be/yt_sched_vid_01",
    )
    results = scheduler.schedule_batch(
        media_ids=["med_sched_01"],
        channel_id="UC_SCHED_01",
        start_date=future_date,
        start_time="10:00",
        timezone_str="UTC",
        confirm_rights=True,
    )
    assert len(results) == 1
    assert results[0]["status"] == "SCHEDULED"
    assert results[0]["youtube_video_id"] == "yt_sched_vid_01"

    # Verify schedule entry saved in DB
    entries = sched_db.get_all_schedules()
    assert len(entries) >= 1
    assert entries[0].channel_id == "UC_SCHED_01"
    assert entries[0].status == PublishStatus.SCHEDULED


def test_reschedule_and_cancel_video(sched_db: Database, tmp_path: Path):
    """Test modifying future slot time remotely and cancelling scheduled upload."""
    account = PublishingAccount(
        id="UC_SCHED_02",
        channel_id="UC_SCHED_02",
        channel_title="Reschedule Channel",
        token_storage_key="file:UC_SCHED_02",
    )
    sched_db.upsert_publishing_account(account)

    video_file = tmp_path / "resched_video.mp4"
    video_file.write_bytes(b"RESCHEDULE_VIDEO_CONTENT")
    sha = compute_file_sha256(video_file)

    media = MediaItem(
        id="med_resched_02",
        file_path=str(video_file),
        file_size=video_file.stat().st_size,
        sha256=sha,
        title="Rescheduled Reel",
    )
    sched_db.upsert_media_item(media)

    future_iso = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%dT12:00:00Z")
    job = PublishingJob(
        id="job_resched_02",
        media_id="med_resched_02",
        channel_id="UC_SCHED_02",
        scheduled_publish_at=future_iso,
        display_timezone="UTC",
        rights_confirmed=True,
        youtube_video_id="yt_resched_vid_99",
    )
    sched_db.upsert_publishing_job(job)

    mock_auth = MagicMock()
    mock_service = MagicMock()
    mock_auth.build_service.return_value = mock_service

    scheduler = YouTubeScheduler(
        db=sched_db,
        auth_manager=mock_auth,
    )

    # Reschedule to 5 days in future
    new_future_date = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")
    success = scheduler.reschedule_video(
        job_or_media_id=job.id,
        new_date=new_future_date,
        new_time="15:30",
        timezone_str="UTC",
    )
    assert success is True
    assert mock_service.videos().update.called

    # Verify job record updated
    updated_job = sched_db.get_publishing_job(job.id)
    assert updated_job.scheduled_publish_at.startswith(new_future_date)

    # Cancel schedule
    cancel_success = scheduler.cancel_schedule(job.id)
    assert cancel_success is True
    cancelled_job = sched_db.get_publishing_job(job.id)
    assert cancelled_job.status == PublishStatus.CANCELLED
