"""Tests for SQLite persistence, migrations, and concurrency."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

from loopstash.database import Database
from loopstash.models import DownloadStatus, JobRecord


def test_database_creation_and_migration(test_db: Database):
    """Test table creation and schema migration verification."""
    assert test_db.verify_integrity() is True

    # Check migration version
    cur = test_db.conn.cursor()
    cur.execute("SELECT MAX(version) FROM schema_migrations")
    assert cur.fetchone()[0] == 1


def test_profile_and_reel_crud(test_db: Database, sample_reel):
    """Test inserting profiles, inserting reels, and retrieving them."""
    test_db.upsert_profile(sample_reel.username, sample_reel.profile_url)

    job = JobRecord(
        shortcode=sample_reel.shortcode,
        username=sample_reel.username,
        profile_url=sample_reel.profile_url,
        reel_url=sample_reel.reel_url,
        media_id=sample_reel.media_id,
        status=DownloadStatus.PENDING,
        upload_timestamp=sample_reel.upload_timestamp,
    )
    test_db.upsert_reel(job)

    retrieved = test_db.get_reel(sample_reel.shortcode)
    assert retrieved is not None
    assert retrieved.shortcode == sample_reel.shortcode
    assert retrieved.status == DownloadStatus.PENDING
    assert retrieved.username == sample_reel.username


def test_stale_job_recovery(test_db: Database, sample_reel):
    """Test that interrupted DOWNLOADING or DISCOVERING jobs are automatically reset to PENDING."""
    test_db.upsert_profile(sample_reel.username, sample_reel.profile_url)

    job = JobRecord(
        shortcode=sample_reel.shortcode,
        username=sample_reel.username,
        profile_url=sample_reel.profile_url,
        reel_url=sample_reel.reel_url,
        status=DownloadStatus.DOWNLOADING,
    )
    test_db.upsert_reel(job)

    # Reconcile stale jobs
    recovered_count = test_db.reconcile_stale_jobs()
    assert recovered_count >= 1

    retrieved = test_db.get_reel(sample_reel.shortcode)
    assert retrieved.status == DownloadStatus.PENDING
    assert "Interrupted" in (retrieved.last_error or "")


def test_completed_atomic_transaction(test_db: Database, sample_reel):
    """Test marking COMPLETED records file hash atomically."""
    test_db.upsert_profile(sample_reel.username, sample_reel.profile_url)
    job = JobRecord(
        shortcode=sample_reel.shortcode,
        username=sample_reel.username,
        profile_url=sample_reel.profile_url,
        reel_url=sample_reel.reel_url,
        status=DownloadStatus.DOWNLOADING,
    )
    test_db.upsert_reel(job)

    fake_hash = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
    test_db.mark_completed(
        shortcode=sample_reel.shortcode,
        destination_path="/fake/path/reel.mp4",
        file_size=123456,
        sha256_hash=fake_hash,
    )

    retrieved = test_db.get_reel(sample_reel.shortcode)
    assert retrieved.status == DownloadStatus.COMPLETED
    assert retrieved.file_size == 123456
    assert retrieved.sha256_hash == fake_hash

    hash_record = test_db.get_reel_by_hash(fake_hash)
    assert hash_record is not None
    assert hash_record["shortcode"] == sample_reel.shortcode
    assert hash_record["file_size"] == 123456


def test_concurrent_execution_safety(tmp_dir: Path):
    """Test thread concurrency and WAL mode safety with multiple workers writing simultaneously."""
    db_file = tmp_dir / "concurrent_test.db"
    db = Database(db_file)
    db.upsert_profile("concurrent_user", "https://instagram.com/concurrent_user/")

    def worker(worker_id: int):
        worker_db = Database(db_file)
        for i in range(15):
            code = f"short_{worker_id}_{i}"
            record = JobRecord(
                shortcode=code,
                username="concurrent_user",
                profile_url="https://instagram.com/concurrent_user/",
                reel_url=f"https://instagram.com/reel/{code}/",
                status=DownloadStatus.PENDING,
            )
            worker_db.upsert_reel(record)
            worker_db.mark_completed(
                shortcode=code,
                destination_path=f"/path/{code}.mp4",
                file_size=1000 + i,
                sha256_hash=f"hash_{worker_id}_{i}".ljust(64, "0"),
            )
        worker_db.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(worker, w) for w in range(5)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    stats = db.get_statistics()
    assert stats["total_reels"] == 75
    assert stats["completed_reels"] == 75
    assert db.verify_integrity() is True
    db.close()


def test_upsert_reel_status_update_and_force(test_db: Database, sample_reel):
    """Test that upsert_reel updates status and respects force flag."""
    test_db.upsert_profile(sample_reel.username, sample_reel.profile_url)

    # 1. Insert as FAILED
    job = JobRecord(
        shortcode=sample_reel.shortcode,
        username=sample_reel.username,
        profile_url=sample_reel.profile_url,
        reel_url=sample_reel.reel_url,
        status=DownloadStatus.FAILED,
        last_error="Initial network glitch",
    )
    test_db.upsert_reel(job)
    assert test_db.get_reel(sample_reel.shortcode).status == DownloadStatus.FAILED

    # 2. Re-queue as PENDING without force -> status should update to PENDING
    job.status = DownloadStatus.PENDING
    job.last_error = None
    test_db.upsert_reel(job)
    retrieved = test_db.get_reel(sample_reel.shortcode)
    assert retrieved.status == DownloadStatus.PENDING
    assert retrieved.last_error is None

    # 3. Mark COMPLETED
    test_db.mark_completed(
        shortcode=sample_reel.shortcode,
        destination_path="/path/test.mp4",
        file_size=5000,
        sha256_hash="a" * 64,
    )
    assert test_db.get_reel(sample_reel.shortcode).status == DownloadStatus.COMPLETED

    # 4. Upsert with PENDING without force -> should remain COMPLETED
    job.status = DownloadStatus.PENDING
    test_db.upsert_reel(job, force=False)
    assert test_db.get_reel(sample_reel.shortcode).status == DownloadStatus.COMPLETED

    # 5. Upsert with PENDING with force=True -> should update to PENDING
    test_db.upsert_reel(job, force=True)
    assert test_db.get_reel(sample_reel.shortcode).status == DownloadStatus.PENDING


def test_get_all_completed_reels(test_db: Database):
    """Test retrieving all completed reels across multiple profiles."""
    test_db.upsert_profile("user_a", "https://instagram.com/user_a/")
    test_db.upsert_profile("user_b", "https://instagram.com/user_b/")

    test_db.upsert_reel(
        JobRecord("reel_a1", "user_a", "url_a", "reel_url_a1", status=DownloadStatus.PENDING)
    )
    test_db.mark_completed("reel_a1", "/path/a1.mp4", 100, "1" * 64)

    test_db.upsert_reel(
        JobRecord("reel_b1", "user_b", "url_b", "reel_url_b1", status=DownloadStatus.PENDING)
    )
    test_db.mark_completed("reel_b1", "/path/b1.mp4", 200, "2" * 64)

    test_db.upsert_reel(
        JobRecord("reel_failed", "user_b", "url_b", "reel_url_f", status=DownloadStatus.FAILED)
    )

    completed = test_db.get_all_completed_reels()
    assert len(completed) == 2
    shortcodes = [r.shortcode for r in completed]
    assert "reel_a1" in shortcodes
    assert "reel_b1" in shortcodes
