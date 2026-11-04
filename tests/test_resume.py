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
