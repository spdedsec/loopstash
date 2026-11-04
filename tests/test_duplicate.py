"""Tests for duplicate Reel detection, hash matching, and archive integrity verification."""

from __future__ import annotations

from pathlib import Path

from loopstash.database import Database
from loopstash.duplicate import DuplicateDetector
from loopstash.models import DownloadStatus, JobRecord, ReelItem
from loopstash.utils.hashing import compute_file_sha256


def test_reel_duplicate_detection(test_db: Database, sample_reel: ReelItem, tmp_dir: Path):
    """Test duplicate Reel detection across various states."""
    detector = DuplicateDetector(test_db)

    # 1. New Reel
    is_dup, record, reason = detector.check_reel_duplicate(sample_reel.shortcode)
    assert not is_dup
    assert record is None
    assert "New Reel" in reason

    # Insert profile and reel as PENDING
    test_db.upsert_profile(sample_reel.username, sample_reel.profile_url)
    job = JobRecord(
        shortcode=sample_reel.shortcode,
        username=sample_reel.username,
        profile_url=sample_reel.profile_url,
        reel_url=sample_reel.reel_url,
        status=DownloadStatus.PENDING,
    )
    test_db.upsert_reel(job)

    # 2. PENDING status is eligible for download
    is_dup, record, _ = detector.check_reel_duplicate(sample_reel.shortcode)
    assert not is_dup
    assert record is not None
    assert record.status == DownloadStatus.PENDING

    # Create dummy video file
    video_file = tmp_dir / "CwK12345XYZ.mp4"
    video_file.write_bytes(b"TEST_VIDEO_BYTES_12345")
    video_hash = compute_file_sha256(video_file)

    # Mark COMPLETED
    test_db.mark_completed(
        shortcode=sample_reel.shortcode,
        destination_path=str(video_file),
        file_size=video_file.stat().st_size,
        sha256_hash=video_hash,
    )

    # 3. COMPLETED status with existing file is a duplicate
    is_dup, record, reason = detector.check_reel_duplicate(sample_reel.shortcode)
    assert is_dup
    assert record.status == DownloadStatus.COMPLETED
    assert "Already completed and verified" in reason

    # 4. Forced redownload bypasses duplicate check
    is_dup, record, reason = detector.check_reel_duplicate(sample_reel.shortcode, force=True)
    assert not is_dup
    assert "Forced redownload" in reason

    # 5. Missing file on disk causes re-download eligibility
    video_file.unlink()
    is_dup, record, reason = detector.check_reel_duplicate(sample_reel.shortcode)
    assert not is_dup
    assert "File missing from disk" in reason


def test_hash_duplicate_detection(test_db: Database, sample_reel: ReelItem):
    """Test detecting media duplicates via SHA-256 hash."""
    detector = DuplicateDetector(test_db)
    test_db.upsert_profile(sample_reel.username, sample_reel.profile_url)

    job = JobRecord(
        shortcode=sample_reel.shortcode,
        username=sample_reel.username,
        profile_url=sample_reel.profile_url,
        reel_url=sample_reel.reel_url,
        status=DownloadStatus.DOWNLOADING,
    )
    test_db.upsert_reel(job)

    dummy_hash = "11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff"
    assert detector.check_hash_duplicate(dummy_hash) is None

    test_db.mark_completed(
        shortcode=sample_reel.shortcode,
        destination_path="/tmp/test.mp4",
        file_size=5000,
        sha256_hash=dummy_hash,
    )

    match = detector.check_hash_duplicate(dummy_hash)
    assert match is not None
    assert match["shortcode"] == sample_reel.shortcode
    assert match["file_size"] == 5000


def test_verify_archive(test_db: Database, sample_reel: ReelItem, tmp_dir: Path):
    """Test archive verification including OK, missing, empty, and corrupted files."""
    detector = DuplicateDetector(test_db)
    test_db.upsert_profile(sample_reel.username, sample_reel.profile_url)

    # 1. OK reel
    reel1_file = tmp_dir / "reel1.mp4"
    reel1_file.write_bytes(b"VALID_REEL_DATA_1")
    reel1_hash = compute_file_sha256(reel1_file)

    test_db.upsert_reel(
        JobRecord(
            shortcode="reel_ok",
            username=sample_reel.username,
            profile_url=sample_reel.profile_url,
            reel_url="https://instagram.com/reel/reel_ok/",
            status=DownloadStatus.COMPLETED,
            destination_path=str(reel1_file),
            file_size=reel1_file.stat().st_size,
            sha256_hash=reel1_hash,
        )
    )

    # 2. Missing file reel
    test_db.upsert_reel(
        JobRecord(
            shortcode="reel_missing",
            username=sample_reel.username,
            profile_url=sample_reel.profile_url,
            reel_url="https://instagram.com/reel/reel_missing/",
            status=DownloadStatus.COMPLETED,
            destination_path=str(tmp_dir / "does_not_exist.mp4"),
            file_size=1234,
            sha256_hash="somehash",
        )
    )

    # 3. Empty (0-byte) file reel
    empty_file = tmp_dir / "empty.mp4"
    empty_file.touch()
    test_db.upsert_reel(
        JobRecord(
            shortcode="reel_empty",
            username=sample_reel.username,
            profile_url=sample_reel.profile_url,
            reel_url="https://instagram.com/reel/reel_empty/",
            status=DownloadStatus.COMPLETED,
            destination_path=str(empty_file),
            file_size=0,
            sha256_hash="emptyhash",
        )
    )

    # 4. Hash mismatch corrupted reel
    corrupted_file = tmp_dir / "corrupted.mp4"
    corrupted_file.write_bytes(b"MODIFIED_AFTER_DOWNLOAD")
    test_db.upsert_reel(
        JobRecord(
            shortcode="reel_corrupted",
            username=sample_reel.username,
            profile_url=sample_reel.profile_url,
            reel_url="https://instagram.com/reel/reel_corrupted/",
            status=DownloadStatus.COMPLETED,
            destination_path=str(corrupted_file),
            file_size=corrupted_file.stat().st_size,
            sha256_hash="0000000000000000000000000000000000000000000000000000000000000000",
        )
    )

    # Run verification for user
    res = detector.verify_archive(username=sample_reel.username)
    assert res["total_checked"] == 4
    assert res["verified"] == 1
    assert res["missing"] == 1
    assert res["corrupted"] == 2

    # Global verification across all profiles
    global_res = detector.verify_archive(username=None)
    assert global_res["total_checked"] == 4
    assert global_res["verified"] == 1


def test_skipped_duplicate_file_missing_allows_redownload(
    test_db: Database, sample_reel: ReelItem, tmp_dir: Path
):
    """Test that a SKIPPED_DUPLICATE record with missing disk file allows redownloading."""
    detector = DuplicateDetector(test_db)
    test_db.upsert_profile(sample_reel.username, sample_reel.profile_url)

    dest_file = tmp_dir / "dup.mp4"
    dest_file.write_bytes(b"DATA")

    test_db.upsert_reel(
        JobRecord(
            shortcode=sample_reel.shortcode,
            username=sample_reel.username,
            profile_url=sample_reel.profile_url,
            reel_url=sample_reel.reel_url,
            status=DownloadStatus.SKIPPED_DUPLICATE,
            destination_path=str(dest_file),
            file_size=4,
        )
    )

    # When file exists on disk
    is_dup, _, _ = detector.check_reel_duplicate(sample_reel.shortcode)
    assert is_dup is True

    # When file is removed from disk
    dest_file.unlink()
    is_dup, _, reason = detector.check_reel_duplicate(sample_reel.shortcode)
    assert is_dup is False
    assert "File missing from disk" in reason
