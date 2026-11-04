"""Tests for YouTube resumable uploading, rights safeguards, session reconciliation, and quota."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from loopstash.database import Database
from loopstash.models import (
    MediaItem,
    PublishingAccount,
    PublishingJob,
    PublishingMetadata,
    PublishStatus,
    UploadSession,
)
from loopstash.publish.quota import QuotaManager
from loopstash.publish.uploader import YouTubeUploader
from loopstash.utils.hashing import compute_file_sha256


@pytest.fixture
def upload_db(tmp_path: Path) -> Database:
    db_file = tmp_path / "test_upload.db"
    return Database(db_file)


@pytest.fixture
def sample_media(
    upload_db: Database, tmp_path: Path
) -> tuple[MediaItem, PublishingMetadata, PublishingJob]:
    video_file = tmp_path / "upload_test.mp4"
    video_file.write_bytes(b"TEST_VIDEO_BYTES_DATA_STREAM")
    sha = compute_file_sha256(video_file)

    account = PublishingAccount(
        id="UC_TEST_CHANNEL",
        channel_id="UC_TEST_CHANNEL",
        channel_title="Test Channel",
        token_storage_key="file:UC_TEST_CHANNEL",
    )
    upload_db.upsert_publishing_account(account)

    media = MediaItem(
        id="med_upload_01",
        file_path=str(video_file),
        file_size=video_file.stat().st_size,
        sha256=sha,
        title="Test Reel Upload",
    )
    upload_db.upsert_media_item(media)

    meta = PublishingMetadata(
        media_id="med_upload_01",
        title="Awesome Reel on YouTube",
        description="Public demonstration #tech",
        tags=["tech", "demo"],
        privacy_status="public",
        rights_confirmed=True,
    )
    upload_db.upsert_publishing_metadata(meta)

    job = PublishingJob(
        id="job_upload_01",
        media_id="med_upload_01",
        channel_id="UC_TEST_CHANNEL",
        rights_confirmed=True,
    )
    upload_db.upsert_publishing_job(job)

    return media, meta, job


def test_upload_requires_rights_confirmation(
    upload_db: Database, sample_media: tuple[MediaItem, PublishingMetadata, PublishingJob]
):
    """Section 13 Safeguard: Upload must fail fast if copyright rights are not confirmed."""
    media, meta, job = sample_media
    uploader = YouTubeUploader(upload_db)

    # Set rights_confirmed = False
    meta.rights_confirmed = False
    job.rights_confirmed = False
    upload_db.upsert_publishing_metadata(meta)
    upload_db.upsert_publishing_job(job)

    mock_service = MagicMock()

    with pytest.raises(
        PermissionError, match="Rights and copyright permission have not been confirmed"
    ):
        uploader.upload_media(
            job=job,
            media_item=media,
            metadata=meta,
            youtube_service=mock_service,
        )

    # Verify job marked as NEEDS_ATTENTION in DB
    refreshed_job = upload_db.get_publishing_job(job.id)
    assert refreshed_job is not None
    assert refreshed_job.status == PublishStatus.NEEDS_ATTENTION


def test_upload_success_public_visibility(
    upload_db: Database, sample_media: tuple[MediaItem, PublishingMetadata, PublishingJob]
):
    """Test successful resumable upload sets public visibility and persists YouTube video ID."""
    media, meta, job = sample_media
    uploader = YouTubeUploader(upload_db)

    # Mock YouTube API
    mock_service = MagicMock()
    mock_request = MagicMock()
    mock_request.resumable_uri = "https://youtube.googleapis.com/upload/session_123"

    # Simulate next_chunk returning (status, response)
    mock_status = MagicMock()
    mock_status.resumable_progress = 1000
    mock_status.total_size = 1000
    mock_response = {"id": "YT_VIDEO_ABC123"}
    mock_request.next_chunk.side_effect = [(mock_status, None), (None, mock_response)]

    mock_service.videos.return_value.insert.return_value = mock_request
    mock_service.videos.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "YT_VIDEO_ABC123", "status": {"uploadStatus": "uploaded"}}]
    }

    vid_id, vid_url = uploader.upload_media(
        job=job,
        media_item=media,
        metadata=meta,
        youtube_service=mock_service,
    )

    assert vid_id == "YT_VIDEO_ABC123"
    assert vid_url == "https://youtu.be/YT_VIDEO_ABC123"

    # Verify insert call arguments explicitly had status.privacyStatus == "public"
    insert_call_kwargs = mock_service.videos.return_value.insert.call_args[1]
    assert insert_call_kwargs["body"]["status"]["privacyStatus"] == "public"
    assert insert_call_kwargs["body"]["snippet"]["title"] == "Awesome Reel on YouTube"

    # Verify job in DB updated to PUBLISHED
    updated_job = upload_db.get_publishing_job(job.id)
    assert updated_job is not None
    assert updated_job.status == PublishStatus.PUBLISHED
    assert updated_job.youtube_video_id == "YT_VIDEO_ABC123"
    assert updated_job.youtube_video_url == "https://youtu.be/YT_VIDEO_ABC123"


def test_upload_reconciles_existing_session_without_duplication(
    upload_db: Database, sample_media: tuple[MediaItem, PublishingMetadata, PublishingJob]
):
    """Test that existing completed resumable session is reconciled without re-uploading."""
    media, meta, job = sample_media
    uploader = YouTubeUploader(upload_db)

    # Record existing session in database
    session = UploadSession(
        session_uri="https://youtube.googleapis.com/upload/interrupted_session",
        job_id=job.id,
        file_path=media.file_path,
        file_size=media.file_size,
        bytes_uploaded=media.file_size,
        last_chunk_byte=media.file_size,
    )
    upload_db.save_upload_session(session)

    mock_service = MagicMock()

    # Mock requests.put returning 200 with video resource
    mock_put_resp = MagicMock()
    mock_put_resp.status_code = 200
    mock_put_resp.json.return_value = {"id": "RECONCILED_VIDEO_XYZ"}

    with patch("requests.put", return_value=mock_put_resp):
        vid_id, vid_url = uploader.upload_media(
            job=job,
            media_item=media,
            metadata=meta,
            youtube_service=mock_service,
        )

    assert vid_id == "RECONCILED_VIDEO_XYZ"
    assert vid_url == "https://youtu.be/RECONCILED_VIDEO_XYZ"
    # Ensure insert was NEVER called! No duplicate video created!
    mock_service.videos.return_value.insert.assert_not_called()

    # Verify session was cleaned up
    assert upload_db.get_upload_session(job.id) is None


def test_upload_quota_exceeded_preflight(
    upload_db: Database, sample_media: tuple[MediaItem, PublishingMetadata, PublishingJob]
):
    """Test preflight rejection when daily video upload quota is reached."""
    media, meta, job = sample_media
    qm = QuotaManager(upload_db, default_limit=5)
    uploader = YouTubeUploader(upload_db, quota_manager=qm)

    # Exhaust quota
    upload_db.record_quota_usage(upload_units=5, api_requests=5)

    mock_service = MagicMock()
    with pytest.raises(RuntimeError, match="Daily YouTube video upload limit reached"):
        uploader.upload_media(
            job=job,
            media_item=media,
            metadata=meta,
            youtube_service=mock_service,
        )


def test_upload_file_hash_mismatch_fails_safely(
    upload_db: Database, sample_media: tuple[MediaItem, PublishingMetadata, PublishingJob]
):
    """Test that modifying a file on disk after cataloging raises ValueError and halts."""
    media, meta, job = sample_media
    uploader = YouTubeUploader(upload_db)

    # Tamper with file
    Path(media.file_path).write_bytes(b"TAMPERED_MODIFIED_BYTES")

    mock_service = MagicMock()
    with pytest.raises(ValueError, match="File content changed since archiving"):
        uploader.upload_media(
            job=job,
            media_item=media,
            metadata=meta,
            youtube_service=mock_service,
        )
