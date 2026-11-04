"""SQLite persistent state layer for LOOPSTASH."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from loopstash.models import DownloadStatus, JobRecord, ProfileRecord
from loopstash.utils.paths import get_default_data_dir

CURRENT_SCHEMA_VERSION = 1


def get_default_db_path() -> Path:
    """Return the default SQLite database path."""
    return get_default_data_dir() / "loopstash.db"


class Database:
    """Thread-safe SQLite database manager for tracking reels, profiles, and hashes."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or get_default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_connection()
        self.migrate()
        self.reconcile_stale_jobs()

    def _init_connection(self) -> None:
        """Establish SQLite connection with WAL mode and sensible timeouts."""
        self.conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False,
            isolation_level=None,  # Autocommit mode, transactions managed explicitly
        )
        self.conn.row_factory = sqlite3.Row
        with self.conn:
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA busy_timeout=10000;")
            self.conn.execute("PRAGMA foreign_keys=ON;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        """Context manager for atomic SQLite transactions."""
        cursor = self.conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        try:
            yield cursor
            cursor.execute("COMMIT")
        except Exception:
            cursor.execute("ROLLBACK")
            raise
        finally:
            cursor.close()

    def verify_integrity(self) -> bool:
        """Check database integrity using SQLite PRAGMA integrity_check."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("PRAGMA integrity_check")
            result = cursor.fetchone()
            return result is not None and result[0] == "ok"
        except Exception:
            return False

    def migrate(self) -> None:
        """Execute database schema migrations."""
        with self.transaction() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
            """)

            cur.execute("SELECT MAX(version) FROM schema_migrations")
            row = cur.fetchone()
            current_ver = row[0] if row and row[0] is not None else 0

            if current_ver < 1:
                # Schema version 1
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS profiles (
                        username TEXT PRIMARY KEY,
                        profile_url TEXT NOT NULL,
                        first_seen TEXT NOT NULL,
                        last_sync TEXT,
                        total_discovered INTEGER DEFAULT 0
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS reels (
                        shortcode TEXT PRIMARY KEY,
                        username TEXT NOT NULL,
                        profile_url TEXT NOT NULL,
                        media_id TEXT,
                        reel_url TEXT NOT NULL,
                        status TEXT NOT NULL,
                        destination_path TEXT,
                        file_size INTEGER,
                        sha256_hash TEXT,
                        upload_timestamp TEXT,
                        download_timestamp TEXT,
                        retry_count INTEGER DEFAULT 0,
                        last_error TEXT,
                        backend TEXT DEFAULT 'gallery-dl',
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (username) REFERENCES profiles(username) ON DELETE CASCADE
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS file_hashes (
                        sha256 TEXT PRIMARY KEY,
                        shortcode TEXT NOT NULL,
                        file_path TEXT NOT NULL,
                        file_size INTEGER NOT NULL,
                        first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (shortcode) REFERENCES reels(shortcode) ON DELETE CASCADE
                    )
                """)

                cur.execute("CREATE INDEX IF NOT EXISTS idx_reels_username ON reels(username)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_reels_status ON reels(status)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_reels_sha256 ON reels(sha256_hash)")

                now_iso = datetime.now(timezone.utc).isoformat()
                cur.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (1, ?)",
                    (now_iso,),
                )

    def reconcile_stale_jobs(self) -> int:
        """Reset jobs interrupted in DOWNLOADING or DISCOVERING back to PENDING."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE reels
                SET status = 'PENDING',
                    updated_at = ?,
                    last_error = 'Interrupted prior to completion; automatically queued for resume'
                WHERE status IN ('DOWNLOADING', 'DISCOVERING')
                """,
                (now_iso,),
            )
            return cur.rowcount

    def upsert_profile(self, username: str, profile_url: str) -> None:
        """Insert or update a profile record."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO profiles (username, profile_url, first_seen, last_sync)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    profile_url = excluded.profile_url,
                    last_sync = excluded.last_sync
                """,
                (username, profile_url, now_iso, now_iso),
            )

    def update_profile_sync(self, username: str, total_discovered: int) -> None:
        """Update last sync timestamp and total discovered count for a profile."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE profiles
                SET last_sync = ?,
                    total_discovered = ?
                WHERE username = ?
                """,
                (now_iso, total_discovered, username),
            )

    def upsert_reel(self, job: JobRecord) -> None:
        """Insert or update a Reel job record without overwriting completed status."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO reels (
                    shortcode, username, profile_url, media_id, reel_url,
                    status, destination_path, file_size, sha256_hash,
                    upload_timestamp, download_timestamp, retry_count,
                    last_error, backend, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(shortcode) DO UPDATE SET
                    media_id = COALESCE(excluded.media_id, reels.media_id),
                    upload_timestamp = COALESCE(excluded.upload_timestamp, reels.upload_timestamp),
                    updated_at = ?
                WHERE reels.status != 'COMPLETED'
                """,
                (
                    job.shortcode,
                    job.username,
                    job.profile_url,
                    job.media_id,
                    job.reel_url,
                    job.status.value,
                    job.destination_path,
                    job.file_size,
                    job.sha256_hash,
                    job.upload_timestamp,
                    job.download_timestamp,
                    job.retry_count,
                    job.last_error,
                    job.backend,
                    now_iso,
                    now_iso,
                    now_iso,
                ),
            )

    def get_reel(self, shortcode: str) -> Optional[JobRecord]:
        """Retrieve a Reel job record by shortcode."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT shortcode, username, profile_url, media_id, reel_url,
                   status, destination_path, file_size, sha256_hash,
                   upload_timestamp, download_timestamp, retry_count,
                   last_error, backend, created_at, updated_at
            FROM reels WHERE shortcode = ?
            """,
            (shortcode,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return JobRecord(
            shortcode=row["shortcode"],
            username=row["username"],
            profile_url=row["profile_url"],
            media_id=row["media_id"],
            reel_url=row["reel_url"],
            status=DownloadStatus(row["status"]),
            destination_path=row["destination_path"],
            file_size=row["file_size"],
            sha256_hash=row["sha256_hash"],
            upload_timestamp=row["upload_timestamp"],
            download_timestamp=row["download_timestamp"],
            retry_count=row["retry_count"],
            last_error=row["last_error"],
            backend=row["backend"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_reels_for_profile(
        self, username: str, status: Optional[DownloadStatus] = None
    ) -> list[JobRecord]:
        """Fetch all reels for a profile, optionally filtered by status."""
        cursor = self.conn.cursor()
        if status:
            cursor.execute(
                """
                SELECT shortcode, username, profile_url, media_id, reel_url,
                       status, destination_path, file_size, sha256_hash,
                       upload_timestamp, download_timestamp, retry_count,
                       last_error, backend, created_at, updated_at
                FROM reels WHERE username = ? AND status = ?
                ORDER BY created_at ASC
                """,
                (username, status.value),
            )
        else:
            cursor.execute(
                """
                SELECT shortcode, username, profile_url, media_id, reel_url,
                       status, destination_path, file_size, sha256_hash,
                       upload_timestamp, download_timestamp, retry_count,
                       last_error, backend, created_at, updated_at
                FROM reels WHERE username = ?
                ORDER BY created_at ASC
                """,
                (username,),
            )
        reels = []
        for row in cursor.fetchall():
            reels.append(
                JobRecord(
                    shortcode=row["shortcode"],
                    username=row["username"],
                    profile_url=row["profile_url"],
                    media_id=row["media_id"],
                    reel_url=row["reel_url"],
                    status=DownloadStatus(row["status"]),
                    destination_path=row["destination_path"],
                    file_size=row["file_size"],
                    sha256_hash=row["sha256_hash"],
                    upload_timestamp=row["upload_timestamp"],
                    download_timestamp=row["download_timestamp"],
                    retry_count=row["retry_count"],
                    last_error=row["last_error"],
                    backend=row["backend"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )
        return reels

    def get_resumable_reels(self, username: Optional[str] = None) -> list[JobRecord]:
        """Retrieve all reels eligible for download or retry (PENDING, FAILED)."""
        cursor = self.conn.cursor()
        if username:
            cursor.execute(
                """
                SELECT shortcode, username, profile_url, media_id, reel_url,
                       status, destination_path, file_size, sha256_hash,
                       upload_timestamp, download_timestamp, retry_count,
                       last_error, backend, created_at, updated_at
                FROM reels
                WHERE username = ? AND status IN ('PENDING', 'FAILED')
                ORDER BY retry_count ASC, created_at ASC
                """,
                (username,),
            )
        else:
            cursor.execute(
                """
                SELECT shortcode, username, profile_url, media_id, reel_url,
                       status, destination_path, file_size, sha256_hash,
                       upload_timestamp, download_timestamp, retry_count,
                       last_error, backend, created_at, updated_at
                FROM reels
                WHERE status IN ('PENDING', 'FAILED')
                ORDER BY retry_count ASC, created_at ASC
                """
            )
        return [
            JobRecord(
                shortcode=row["shortcode"],
                username=row["username"],
                profile_url=row["profile_url"],
                media_id=row["media_id"],
                reel_url=row["reel_url"],
                status=DownloadStatus(row["status"]),
                destination_path=row["destination_path"],
                file_size=row["file_size"],
                sha256_hash=row["sha256_hash"],
                upload_timestamp=row["upload_timestamp"],
                download_timestamp=row["download_timestamp"],
                retry_count=row["retry_count"],
                last_error=row["last_error"],
                backend=row["backend"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in cursor.fetchall()
        ]

    def mark_downloading(self, shortcode: str) -> None:
        """Mark a Reel as actively downloading."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE reels
                SET status = 'DOWNLOADING', updated_at = ?
                WHERE shortcode = ?
                """,
                (now_iso, shortcode),
            )

    def mark_completed(
        self,
        shortcode: str,
        destination_path: str,
        file_size: int,
        sha256_hash: str,
        download_timestamp: Optional[str] = None,
    ) -> None:
        """Atomically mark a Reel as completed and record its file SHA-256."""
        now_iso = download_timestamp or datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE reels
                SET status = 'COMPLETED',
                    destination_path = ?,
                    file_size = ?,
                    sha256_hash = ?,
                    download_timestamp = ?,
                    last_error = NULL,
                    updated_at = ?
                WHERE shortcode = ?
                """,
                (destination_path, file_size, sha256_hash, now_iso, now_iso, shortcode),
            )
            cur.execute(
                """
                INSERT INTO file_hashes (sha256, shortcode, file_path, file_size, first_seen)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(sha256) DO UPDATE SET
                    file_path = excluded.file_path,
                    file_size = excluded.file_size
                """,
                (sha256_hash, shortcode, destination_path, file_size, now_iso),
            )

    def mark_failed(self, shortcode: str, error: str, increment_retry: bool = True) -> None:
        """Record download failure and increment retry counter."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            if increment_retry:
                cur.execute(
                    """
                    UPDATE reels
                    SET status = 'FAILED',
                        retry_count = retry_count + 1,
                        last_error = ?,
                        updated_at = ?
                    WHERE shortcode = ?
                    """,
                    (error, now_iso, shortcode),
                )
            else:
                cur.execute(
                    """
                    UPDATE reels
                    SET status = 'FAILED',
                        last_error = ?,
                        updated_at = ?
                    WHERE shortcode = ?
                    """,
                    (error, now_iso, shortcode),
                )

    def mark_skipped_duplicate(
        self,
        shortcode: str,
        destination_path: Optional[str] = None,
        sha256_hash: Optional[str] = None,
    ) -> None:
        """Mark a Reel as skipped duplicate."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE reels
                SET status = 'SKIPPED_DUPLICATE',
                    destination_path = COALESCE(?, destination_path),
                    sha256_hash = COALESCE(?, sha256_hash),
                    updated_at = ?
                WHERE shortcode = ?
                """,
                (destination_path, sha256_hash, now_iso, shortcode),
            )

    def mark_unavailable(self, shortcode: str, error: str) -> None:
        """Mark a Reel as permanently unavailable (e.g. 404 / deleted)."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE reels
                SET status = 'UNAVAILABLE',
                    last_error = ?,
                    updated_at = ?
                WHERE shortcode = ?
                """,
                (error, now_iso, shortcode),
            )

    def get_reel_by_hash(self, sha256_hash: str) -> Optional[dict]:
        """Look up existing record by file SHA-256 hash."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT sha256, shortcode, file_path, file_size, first_seen
            FROM file_hashes WHERE sha256 = ?
            """,
            (sha256_hash.lower(),),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return dict(row)

    def get_all_profiles(self) -> list[ProfileRecord]:
        """Retrieve all tracked profiles with aggregated reel counts."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT p.username, p.profile_url, p.first_seen, p.last_sync, p.total_discovered,
                   COALESCE(SUM(CASE WHEN r.status = 'COMPLETED' THEN 1 ELSE 0 END), 0)
                       AS completed_count,
                   COALESCE(SUM(CASE WHEN r.status = 'FAILED' THEN 1 ELSE 0 END), 0)
                       AS failed_count,
                   COALESCE(SUM(CASE WHEN r.status = 'SKIPPED_DUPLICATE' THEN 1 ELSE 0 END), 0)
                       AS duplicate_count
            FROM profiles p
            LEFT JOIN reels r ON p.username = r.username
            GROUP BY p.username
            ORDER BY p.username ASC
            """
        )
        profiles = []
        for row in cursor.fetchall():
            profiles.append(
                ProfileRecord(
                    username=row["username"],
                    profile_url=row["profile_url"],
                    first_seen=row["first_seen"],
                    last_sync=row["last_sync"],
                    total_discovered=row["total_discovered"],
                    completed_count=row["completed_count"],
                    failed_count=row["failed_count"],
                    duplicate_count=row["duplicate_count"],
                )
            )
        return profiles

    def get_profile(self, username: str) -> Optional[ProfileRecord]:
        """Retrieve aggregated stats for a single profile."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT p.username, p.profile_url, p.first_seen, p.last_sync, p.total_discovered,
                   COALESCE(SUM(CASE WHEN r.status = 'COMPLETED' THEN 1 ELSE 0 END), 0)
                       AS completed_count,
                   COALESCE(SUM(CASE WHEN r.status = 'FAILED' THEN 1 ELSE 0 END), 0)
                       AS failed_count,
                   COALESCE(SUM(CASE WHEN r.status = 'SKIPPED_DUPLICATE' THEN 1 ELSE 0 END), 0)
                       AS duplicate_count
            FROM profiles p
            LEFT JOIN reels r ON p.username = r.username
            WHERE p.username = ?
            GROUP BY p.username
            """,
            (username,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return ProfileRecord(
            username=row["username"],
            profile_url=row["profile_url"],
            first_seen=row["first_seen"],
            last_sync=row["last_sync"],
            total_discovered=row["total_discovered"],
            completed_count=row["completed_count"],
            failed_count=row["failed_count"],
            duplicate_count=row["duplicate_count"],
        )

    def get_history(self, limit: int = 50) -> list[JobRecord]:
        """Fetch recently completed or updated reels."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT shortcode, username, profile_url, media_id, reel_url,
                   status, destination_path, file_size, sha256_hash,
                   upload_timestamp, download_timestamp, retry_count,
                   last_error, backend, created_at, updated_at
            FROM reels
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            JobRecord(
                shortcode=row["shortcode"],
                username=row["username"],
                profile_url=row["profile_url"],
                media_id=row["media_id"],
                reel_url=row["reel_url"],
                status=DownloadStatus(row["status"]),
                destination_path=row["destination_path"],
                file_size=row["file_size"],
                sha256_hash=row["sha256_hash"],
                upload_timestamp=row["upload_timestamp"],
                download_timestamp=row["download_timestamp"],
                retry_count=row["retry_count"],
                last_error=row["last_error"],
                backend=row["backend"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in cursor.fetchall()
        ]

    def get_statistics(self) -> dict:
        """Return system-wide download statistics."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM profiles")
        total_profiles = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM reels")
        total_reels = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM reels WHERE status = 'COMPLETED'")
        completed_reels = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM reels WHERE status = 'FAILED'")
        failed_reels = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM reels WHERE status = 'SKIPPED_DUPLICATE'")
        duplicate_reels = cursor.fetchone()[0]

        cursor.execute("SELECT COALESCE(SUM(file_size), 0) FROM reels WHERE status = 'COMPLETED'")
        total_bytes = cursor.fetchone()[0]

        return {
            "total_profiles": total_profiles,
            "total_reels": total_reels,
            "completed_reels": completed_reels,
            "failed_reels": failed_reels,
            "duplicate_reels": duplicate_reels,
            "total_bytes": total_bytes,
        }

    def close(self) -> None:
        """Close SQLite connection cleanly."""
        try:
            self.conn.close()
        except Exception:
            pass
