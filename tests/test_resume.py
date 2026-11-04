"""Tests for crash recovery, job interruption, and resume functionality."""

from __future__ import annotations

import sys
from pathlib import Path

from loopstash.backends.base import AuthenticationRequiredError
from loopstash.database import Database
from loopstash.downloader import Downloader
from loopstash.models import DownloadStatus, JobRecord, ReelItem
from loopstash.retry import RetryPolicy
from loopstash.storage import StorageManager

sys.path.insert(0, str(Path(__file__).parent))
from conftest import MockExtractionBackend


def test_resume_orchestration(tmp_dir: Path, test_db: Database):
    """Test resuming an interrupted profile download without repeating completed items."""
    storage = StorageManager(tmp_dir / "storage")

    # Create 5 sample reels for a creator
    username = "speedcuber"
    profile_url = f"https://www.instagram.com/{username}/"
    test_db.upsert_profile(username, profile_url)

    reels = [
        ReelItem(
            shortcode=f"REEL_{i:02d}",
            username=username,
            reel_url=f"https://www.instagram.com/reel/REEL_{i:02d}/",
            profile_url=profile_url,
        )
        for i in range(1, 6)
    ]

    # Pre-populate state:
    # REEL_01: COMPLETED
    # REEL_02: COMPLETED
    # REEL_03: DOWNLOADING (simulating crash during download)
    # REEL_04: FAILED (simulating prior network glitch)
    # REEL_05: PENDING

    # Create dummy files for completed ones
    file1 = storage.get_target_file_path(reels[0])
    file1.parent.mkdir(parents=True, exist_ok=True)
    file1.write_bytes(b"EXISTING_VIDEO_1")

    file2 = storage.get_target_file_path(reels[1])
    file2.parent.mkdir(parents=True, exist_ok=True)
    file2.write_bytes(b"EXISTING_VIDEO_2")

    test_db.upsert_reel(
        JobRecord(
            shortcode=reels[0].shortcode,
            username=username,
            profile_url=profile_url,
            reel_url=reels[0].reel_url,
            status=DownloadStatus.COMPLETED,
            destination_path=str(file1),
            file_size=len(b"EXISTING_VIDEO_1"),
            sha256_hash="hash1",
        )
    )
    test_db.upsert_reel(
        JobRecord(
            shortcode=reels[1].shortcode,
            username=username,
            profile_url=profile_url,
            reel_url=reels[1].reel_url,
            status=DownloadStatus.COMPLETED,
            destination_path=str(file2),
            file_size=len(b"EXISTING_VIDEO_2"),
            sha256_hash="hash2",
        )
    )
    test_db.upsert_reel(
        JobRecord(
            shortcode=reels[2].shortcode,
            username=username,
            profile_url=profile_url,
            reel_url=reels[2].reel_url,
            status=DownloadStatus.DOWNLOADING,  # Stale
        )
    )
    test_db.upsert_reel(
        JobRecord(
            shortcode=reels[3].shortcode,
            username=username,
            profile_url=profile_url,
            reel_url=reels[3].reel_url,
            status=DownloadStatus.FAILED,
            last_error="Network timeout",
        )
    )
    test_db.upsert_reel(
        JobRecord(
            shortcode=reels[4].shortcode,
            username=username,
            profile_url=profile_url,
            reel_url=reels[4].reel_url,
            status=DownloadStatus.PENDING,
        )
    )

    mock_backend = MockExtractionBackend(reels=reels)
    retry_policy = RetryPolicy(max_retries=2, base_delay=0.01)
    downloader = Downloader(
        backend=mock_backend,
        storage=storage,
        db=test_db,
        retry_policy=retry_policy,
        rate_limit_pause=0.0,
    )

    # Resume downloading
    summaries = downloader.resume(username=username)

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.username == username
    # Only 3 reels were resumable (REEL_03, REEL_04, REEL_05)
    assert summary.total_discovered == 3
    assert summary.downloaded == 3
    assert summary.failed == 0

    # Verify mock backend only downloaded the 3 missing ones
    assert mock_backend.downloaded_count == 3

    # Verify all 5 are now COMPLETED in DB
    all_reels = test_db.get_reels_for_profile(username)
    assert len(all_reels) == 5
    for r in all_reels:
        assert r.status == DownloadStatus.COMPLETED
        assert Path(r.destination_path).is_file()


def test_resume_preserves_progress_on_fatal_error(tmp_dir: Path, test_db: Database):
    """Test that fatal error (like login required) pauses batch while saving prior completions."""
    storage = StorageManager(tmp_dir / "storage")
    username = "private_creator"
    profile_url = f"https://www.instagram.com/{username}/"
    test_db.upsert_profile(username, profile_url)

    reels = [
        ReelItem(
            shortcode=f"REEL_{i}",
            username=username,
            reel_url=f"https://www.instagram.com/reel/REEL_{i}/",
            profile_url=profile_url,
        )
        for i in range(1, 4)
    ]

    for r in reels:
        test_db.upsert_reel(
            JobRecord(
                shortcode=r.shortcode,
                username=username,
                profile_url=profile_url,
                reel_url=r.reel_url,
                status=DownloadStatus.PENDING,
            )
        )

    # Mock backend that succeeds on reel 1, then raises AuthenticationRequiredError on reel 2
    class FlakyAuthBackend(MockExtractionBackend):
        def download_reel(
            self, reel: ReelItem, target_file: Path, temp_file: Path, browser: str = None
        ) -> Path:
            if reel.shortcode == "REEL_2":
                raise AuthenticationRequiredError("Login challenge required")
            return super().download_reel(reel, target_file, temp_file, browser)

    backend = FlakyAuthBackend(reels=reels)
    downloader = Downloader(
        backend=backend,
        storage=storage,
        db=test_db,
        rate_limit_pause=0.0,
    )

    summaries = downloader.resume(username=username)
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.downloaded == 1
    assert summary.failed == 1

    # REEL_1 should be COMPLETED
    r1 = test_db.get_reel("REEL_1")
    assert r1.status == DownloadStatus.COMPLETED

    # REEL_2 should be FAILED
    r2 = test_db.get_reel("REEL_2")
    assert r2.status == DownloadStatus.FAILED

    # REEL_3 should remain PENDING (not attempted after fatal error halted the batch)
    r3 = test_db.get_reel("REEL_3")
    assert r3.status == DownloadStatus.PENDING


def test_resume_recovers_intact_disk_files(tmp_dir: Path, test_db: Database):
    """Test that resume recovers intact on-disk files without re-downloading."""
    storage = StorageManager(tmp_dir / "storage")
    username = "crashed_user"
    profile_url = f"https://www.instagram.com/{username}/"
    test_db.upsert_profile(username, profile_url)

    reel = ReelItem(
        shortcode="REEL_CRASHED",
        username=username,
        reel_url="https://instagram.com/reel/REEL_CRASHED/",
        profile_url=profile_url,
    )
    # Target file is on disk
    target_file = storage.get_target_file_path(reel)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_bytes(b"ALREADY_SAVED_VIDEO_ON_DISK")

    # DB record is PENDING (e.g. crashed after write, or stale job reset)
    test_db.upsert_reel(
        JobRecord(
            shortcode=reel.shortcode,
            username=username,
            profile_url=profile_url,
            reel_url=reel.reel_url,
            status=DownloadStatus.PENDING,
        )
    )

    mock_backend = MockExtractionBackend(reels=[reel])
    downloader = Downloader(
        backend=mock_backend,
        storage=storage,
        db=test_db,
    )

    summaries = downloader.resume(username=username)
    assert len(summaries) == 1
    assert summaries[0].downloaded == 1
    # Backend download should NOT have been invoked because file was already on disk
    assert mock_backend.download_attempts == 0

    record = test_db.get_reel(reel.shortcode)
    assert record.status == DownloadStatus.COMPLETED
    assert record.destination_path == str(target_file)
    assert record.file_size == len(b"ALREADY_SAVED_VIDEO_ON_DISK")


def test_dry_run_does_not_mutate_database(tmp_dir: Path, test_db: Database):
    """Test that dry-run does not write profiles or queue reels into the database."""
    storage = StorageManager(tmp_dir / "storage")
    username = "dryrunuser"
    profile_url = f"https://www.instagram.com/{username}/"
    reel = ReelItem(
        shortcode="DRY_REEL_1",
        username=username,
        reel_url="https://instagram.com/reel/DRY_REEL_1/",
        profile_url=profile_url,
    )

    mock_backend = MockExtractionBackend(reels=[reel])
    downloader = Downloader(
        backend=mock_backend,
        storage=storage,
        db=test_db,
    )

    summary = downloader.download_profile(username, dry_run=True)
    assert summary.is_dry_run is True
    assert summary.total_discovered == 1
    assert summary.downloaded == 0

    # Ensure nothing was stored in the DB
    assert test_db.get_profile(username) is None
    assert test_db.get_reel("DRY_REEL_1") is None
    assert len(test_db.get_resumable_reels()) == 0


def test_download_preserves_full_metadata(tmp_dir: Path, test_db: Database):
    """Test that Downloader preserves caption, hashtags, derived title,
    dimensions, duration, and raw json.
    """
    storage = StorageManager(tmp_dir / "storage")
    username = "artisan_creator"
    profile_url = f"https://www.instagram.com/{username}/"
    reel = ReelItem(
        shortcode="META_PRESERVE_01",
        username=username,
        reel_url="https://instagram.com/reel/META_PRESERVE_01/",
        profile_url=profile_url,
        title="Original Video Title",
        caption="Crafting handmade ceramic mugs from local clay #ceramics #pottery #craft",
        duration=42.5,
        width=1080,
        height=1920,
        extra_metadata={"custom_id": "ceramic_01", "fps": 60},
    )

    mock_backend = MockExtractionBackend(reels=[reel])
    downloader = Downloader(
        backend=mock_backend,
        storage=storage,
        db=test_db,
    )

    summary = downloader.download_profile(username)
    assert summary.downloaded == 1

    # Verify media_items table has caption, duration, dimensions
    media = test_db.get_media_item("META_PRESERVE_01")
    assert media is not None
    assert (
        media.caption == "Crafting handmade ceramic mugs from local clay #ceramics #pottery #craft"
    )
    assert media.duration == 42.5
    assert media.width == 1080
    assert media.height == 1920
    assert "Crafting handmade ceramic mugs" in media.title

    # Verify publishing_metadata has derived title, tags, and attribution description
    pub_meta = test_db.get_publishing_metadata("META_PRESERVE_01")
    assert pub_meta is not None
    assert "Crafting handmade ceramic mugs" in pub_meta.title
    assert "ceramics" in pub_meta.tags
    assert "pottery" in pub_meta.tags
    assert "Original source:" in pub_meta.description

    # Verify media_metadata raw JSON
    raw_meta = test_db.get_media_metadata("META_PRESERVE_01")
    assert raw_meta is not None
    assert raw_meta.raw_json.get("custom_id") == "ceramic_01"
