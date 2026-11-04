"""SQLite persistent state layer for LOOPSTASH."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from loopstash.models import (
    DownloadStatus,
    JobRecord,
    MediaItem,
    MediaMetadata,
    ProfileRecord,
    PublishingAccount,
    PublishingJob,
    PublishingMetadata,
    PublishStatus,
    QuotaRecord,
    ScheduleEntry,
    UploadSession,
)
from loopstash.utils.paths import get_default_data_dir

CURRENT_SCHEMA_VERSION = 1


def get_default_db_path() -> Path:
    """Return the default SQLite database path."""
    return get_default_data_dir() / "loopstash.db"


def _extract_hashtags(caption: Optional[str]) -> list[str]:
    """Extract hashtags from caption string."""
    if not caption:
        return []
    tags = []
    for word in caption.split():
        if word.startswith("#") and len(word) > 1:
            clean = word.lstrip("#").strip(".,!?:;\"'()[]{}")
            if clean and clean not in tags:
                tags.append(clean)
    return tags


def _derive_title(caption: Optional[str], shortcode: str) -> str:
    """Derive a usable title from first meaningful caption line or fallback."""
    if caption:
        for line in caption.splitlines():
            clean = line.strip()
            if clean:
                if len(clean) > 100:
                    clean = clean[:97] + "..."
                return clean
    return f"Reel {shortcode}"


class Database:
    """Thread-safe SQLite database manager for reels, profiles, library items, and jobs."""

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

            now_iso = datetime.now(timezone.utc).isoformat()

            if current_ver < 1:
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

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS media_items (
                        id TEXT PRIMARY KEY,
                        source_platform TEXT NOT NULL DEFAULT 'instagram',
                        source_id TEXT NOT NULL,
                        username TEXT,
                        title TEXT,
                        caption TEXT,
                        file_path TEXT,
                        file_size INTEGER,
                        sha256 TEXT,
                        duration REAL,
                        width INTEGER,
                        height INTEGER,
                        upload_timestamp TEXT,
                        download_timestamp TEXT,
                        is_excluded INTEGER DEFAULT 0,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS media_metadata (
                        media_id TEXT PRIMARY KEY,
                        raw_json TEXT DEFAULT '{}',
                        hashtags TEXT DEFAULT '[]',
                        audio_info TEXT,
                        thumbnail_url TEXT,
                        thumbnail_path TEXT,
                        extra TEXT DEFAULT '{}',
                        FOREIGN KEY (media_id) REFERENCES media_items(id) ON DELETE CASCADE
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS publishing_accounts (
                        id TEXT PRIMARY KEY,
                        platform TEXT NOT NULL DEFAULT 'youtube',
                        account_name TEXT NOT NULL,
                        channel_id TEXT NOT NULL UNIQUE,
                        channel_title TEXT NOT NULL,
                        token_storage_key TEXT NOT NULL,
                        is_active INTEGER DEFAULT 1,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS publishing_metadata (
                        media_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        description TEXT,
                        tags TEXT DEFAULT '[]',
                        category_id TEXT DEFAULT '22',
                        privacy_status TEXT DEFAULT 'public',
                        made_for_kids INTEGER DEFAULT 0,
                        thumbnail_path TEXT,
                        language TEXT,
                        rights_confirmed INTEGER DEFAULT 0,
                        rights_notes TEXT,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (media_id) REFERENCES media_items(id) ON DELETE CASCADE
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS publishing_jobs (
                        id TEXT PRIMARY KEY,
                        media_id TEXT NOT NULL,
                        channel_id TEXT NOT NULL,
                        platform TEXT NOT NULL DEFAULT 'youtube',
                        status TEXT NOT NULL DEFAULT 'READY',
                        scheduled_publish_at TEXT,
                        display_timezone TEXT,
                        upload_session_uri TEXT,
                        bytes_uploaded INTEGER DEFAULT 0,
                        youtube_video_id TEXT,
                        youtube_video_url TEXT,
                        publication_date TEXT,
                        retry_count INTEGER DEFAULT 0,
                        last_error TEXT,
                        error_type TEXT,
                        rights_confirmed INTEGER DEFAULT 0,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        completed_at TEXT,
                        FOREIGN KEY (media_id) REFERENCES media_items(id) ON DELETE CASCADE,
                        FOREIGN KEY (channel_id)
                            REFERENCES publishing_accounts(channel_id) ON DELETE CASCADE
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS schedule_entries (
                        id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL,
                        media_id TEXT NOT NULL,
                        channel_id TEXT NOT NULL,
                        scheduled_utc TEXT NOT NULL,
                        local_display_time TEXT NOT NULL,
                        timezone_name TEXT NOT NULL,
                        interval_spec TEXT DEFAULT 'daily',
                        status TEXT NOT NULL DEFAULT 'SCHEDULED',
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (job_id) REFERENCES publishing_jobs(id) ON DELETE CASCADE,
                        FOREIGN KEY (media_id) REFERENCES media_items(id) ON DELETE CASCADE
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS upload_sessions (
                        session_uri TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL,
                        file_path TEXT NOT NULL,
                        file_size INTEGER NOT NULL,
                        bytes_uploaded INTEGER DEFAULT 0,
                        last_chunk_byte INTEGER DEFAULT 0,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (job_id) REFERENCES publishing_jobs(id) ON DELETE CASCADE
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS job_errors (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL,
                        error_code TEXT,
                        error_message TEXT NOT NULL,
                        error_type TEXT,
                        retry_after INTEGER,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (job_id) REFERENCES publishing_jobs(id) ON DELETE CASCADE
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS quota_usage (
                        date_utc TEXT PRIMARY KEY,
                        upload_units_used INTEGER DEFAULT 0,
                        api_requests_count INTEGER DEFAULT 0,
                        daily_limit INTEGER DEFAULT 100,
                        last_request_time TEXT
                    )
                """)

                cur.execute("CREATE INDEX IF NOT EXISTS idx_media_user ON media_items(username)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pub_jobs_status ON publishing_jobs(status)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pub_jobs_media ON publishing_jobs(media_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_sched_time ON schedule_entries(scheduled_utc)"
                )

                cur.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (1, ?)",
                    (now_iso,),
                )

    def reconcile_stale_jobs(self) -> int:
        """Reset jobs interrupted prior to completion."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE reels
                SET status = 'PENDING',
                    updated_at = ?,
                    last_error = 'Interrupted prior to completion; queued for resume'
                WHERE status IN ('DOWNLOADING', 'DISCOVERING')
                """,
                (now_iso,),
            )
            reels_reconciled = cur.rowcount

            cur.execute(
                """
                UPDATE publishing_jobs
                SET status = 'UPLOAD_INTERRUPTED',
                    updated_at = ?,
                    last_error = 'Upload interrupted prior to completion'
                WHERE status = 'UPLOADING'
                """,
                (now_iso,),
            )
            return reels_reconciled

    # --- Profile & Reel Archive Methods ---

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

    def upsert_reel(self, job: JobRecord, force: bool = False) -> None:
        """Insert or update a Reel job record."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            if force:
                cur.execute(
                    """
                    INSERT INTO reels (
                        shortcode, username, profile_url, media_id, reel_url,
                        status, destination_path, file_size, sha256_hash,
                        upload_timestamp, download_timestamp, retry_count,
                        last_error, backend, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(shortcode) DO UPDATE SET
                        profile_url = excluded.profile_url,
                        media_id = COALESCE(excluded.media_id, reels.media_id),
                        reel_url = excluded.reel_url,
                        status = excluded.status,
                        upload_timestamp = COALESCE(
                            excluded.upload_timestamp, reels.upload_timestamp
                        ),
                        last_error = excluded.last_error,
                        updated_at = ?
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
            else:
                cur.execute(
                    """
                    INSERT INTO reels (
                        shortcode, username, profile_url, media_id, reel_url,
                        status, destination_path, file_size, sha256_hash,
                        upload_timestamp, download_timestamp, retry_count,
                        last_error, backend, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(shortcode) DO UPDATE SET
                        profile_url = excluded.profile_url,
                        media_id = COALESCE(excluded.media_id, reels.media_id),
                        reel_url = excluded.reel_url,
                        status = excluded.status,
                        upload_timestamp = COALESCE(
                            excluded.upload_timestamp, reels.upload_timestamp
                        ),
                        last_error = excluded.last_error,
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
                "UPDATE reels SET status = 'DOWNLOADING', updated_at = ? WHERE shortcode = ?",
                (now_iso, shortcode),
            )

    def mark_completed(
        self,
        shortcode: str,
        destination_path: str,
        file_size: int,
        sha256_hash: str,
        download_timestamp: Optional[str] = None,
        caption: Optional[str] = None,
        duration: Optional[float] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        raw_metadata: Optional[dict] = None,
    ) -> None:
        """Atomically mark Reel completed and register into media library."""
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

            cur.execute(
                "SELECT username, upload_timestamp, reel_url FROM reels WHERE shortcode = ?",
                (shortcode,),
            )
            reel_row = cur.fetchone()
            uname = reel_row["username"] if reel_row else "unknown"
            upload_ts = reel_row["upload_timestamp"] if reel_row else None

            derived_title = _derive_title(caption, shortcode)
            tags = _extract_hashtags(caption)
            source_url = (
                reel_row["reel_url"] if reel_row else f"https://www.instagram.com/reel/{shortcode}/"
            )
            desc_text = (
                f"{caption}\n\nOriginal source: {source_url}"
                if caption
                else f"Original source: {source_url}"
            )

            cur.execute(
                """
                INSERT INTO media_items (
                    id, source_platform, source_id, username, title, caption,
                    file_path, file_size, sha256, duration, width, height,
                    upload_timestamp, download_timestamp, updated_at
                ) VALUES (?, 'instagram', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    file_path = excluded.file_path,
                    file_size = excluded.file_size,
                    sha256 = excluded.sha256,
                    duration = COALESCE(excluded.duration, media_items.duration),
                    width = COALESCE(excluded.width, media_items.width),
                    height = COALESCE(excluded.height, media_items.height),
                    caption = COALESCE(excluded.caption, media_items.caption),
                    title = COALESCE(excluded.title, media_items.title),
                    updated_at = excluded.updated_at
                """,
                (
                    shortcode,
                    shortcode,
                    uname,
                    derived_title,
                    caption,
                    destination_path,
                    file_size,
                    sha256_hash,
                    duration,
                    width,
                    height,
                    upload_ts,
                    now_iso,
                    now_iso,
                ),
            )

            cur.execute(
                """
                INSERT INTO publishing_metadata (
                    media_id, title, description, tags, category_id, privacy_status, updated_at
                ) VALUES (?, ?, ?, ?, '22', 'public', ?)
                ON CONFLICT(media_id) DO UPDATE SET
                    title = CASE WHEN publishing_metadata.title = ''
                                 THEN excluded.title ELSE publishing_metadata.title END,
                    description = CASE WHEN publishing_metadata.description = ''
                                       THEN excluded.description
                                       ELSE publishing_metadata.description END,
                    tags = CASE WHEN publishing_metadata.tags = '[]'
                                THEN excluded.tags ELSE publishing_metadata.tags END,
                    updated_at = excluded.updated_at
                """,
                (shortcode, derived_title, desc_text, json.dumps(tags), now_iso),
            )

            raw_json_str = json.dumps(raw_metadata or {})
            cur.execute(
                """
                INSERT INTO media_metadata (media_id, raw_json, hashtags, extra)
                VALUES (?, ?, ?, '{}')
                ON CONFLICT(media_id) DO UPDATE SET
                    raw_json = excluded.raw_json,
                    hashtags = excluded.hashtags
                """,
                (shortcode, raw_json_str, json.dumps(tags)),
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
                SET status = 'UNAVAILABLE', last_error = ?, updated_at = ?
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

    def get_all_completed_reels(self) -> list[JobRecord]:
        """Fetch all completed reels across all profiles."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT shortcode, username, profile_url, media_id, reel_url,
                   status, destination_path, file_size, sha256_hash,
                   upload_timestamp, download_timestamp, retry_count,
                   last_error, backend, created_at, updated_at
            FROM reels WHERE status = 'COMPLETED'
            ORDER BY created_at ASC
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
        return [
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
            for row in cursor.fetchall()
        ]

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

    # --- Media Library CRUD Methods ---

    def upsert_media_item(self, item: MediaItem) -> None:
        """Insert or update a cataloged media item."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO media_items (
                    id, source_platform, source_id, username, title, caption,
                    file_path, file_size, sha256, duration, width, height,
                    upload_timestamp, download_timestamp, is_excluded, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_platform = excluded.source_platform,
                    source_id = excluded.source_id,
                    username = COALESCE(excluded.username, media_items.username),
                    title = COALESCE(excluded.title, media_items.title),
                    caption = COALESCE(excluded.caption, media_items.caption),
                    file_path = excluded.file_path,
                    file_size = excluded.file_size,
                    sha256 = excluded.sha256,
                    duration = COALESCE(excluded.duration, media_items.duration),
                    width = COALESCE(excluded.width, media_items.width),
                    height = COALESCE(excluded.height, media_items.height),
                    is_excluded = excluded.is_excluded,
                    updated_at = excluded.updated_at
                """,
                (
                    item.id,
                    item.source_platform,
                    item.source_id,
                    item.username,
                    item.title,
                    item.caption,
                    item.file_path,
                    item.file_size,
                    item.sha256,
                    item.duration,
                    item.width,
                    item.height,
                    item.upload_timestamp,
                    item.download_timestamp,
                    1 if item.is_excluded else 0,
                    item.created_at or now_iso,
                    now_iso,
                ),
            )

    def get_media_item(self, media_id: str) -> Optional[MediaItem]:
        """Retrieve a media item by ID."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, source_platform, source_id, username, title, caption,
                   file_path, file_size, sha256, duration, width, height,
                   upload_timestamp, download_timestamp, is_excluded, created_at, updated_at
            FROM media_items WHERE id = ?
            """,
            (media_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return MediaItem(
            id=row["id"],
            source_platform=row["source_platform"],
            source_id=row["source_id"],
            username=row["username"],
            title=row["title"],
            caption=row["caption"],
            file_path=row["file_path"],
            file_size=row["file_size"],
            sha256=row["sha256"],
            duration=row["duration"],
            width=row["width"],
            height=row["height"],
            upload_timestamp=row["upload_timestamp"],
            download_timestamp=row["download_timestamp"],
            is_excluded=bool(row["is_excluded"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_all_media_items(
        self,
        username: Optional[str] = None,
        excluded: Optional[bool] = None,
        download_status: Optional[str] = None,
        publish_status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MediaItem]:
        """Fetch media items with optional filters including creator, exclusion,
        download status, and publish status.
        """
        cursor = self.conn.cursor()
        conditions = []
        params: list = []

        if username:
            conditions.append("username = ?")
            params.append(username)
        if excluded is not None:
            conditions.append("is_excluded = ?")
            params.append(1 if excluded else 0)
        if download_status:
            conditions.append("id IN (SELECT shortcode FROM reels WHERE status = ?)")
            params.append(download_status.upper().strip())
        if publish_status:
            conditions.append("id IN (SELECT media_id FROM publishing_jobs WHERE status = ?)")
            params.append(publish_status.upper().strip())

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT id, source_platform, source_id, username, title, caption,
                   file_path, file_size, sha256, duration, width, height,
                   upload_timestamp, download_timestamp, is_excluded, created_at, updated_at
            FROM media_items
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        cursor.execute(query, tuple(params))
        return [
            MediaItem(
                id=row["id"],
                source_platform=row["source_platform"],
                source_id=row["source_id"],
                username=row["username"],
                title=row["title"],
                caption=row["caption"],
                file_path=row["file_path"],
                file_size=row["file_size"],
                sha256=row["sha256"],
                duration=row["duration"],
                width=row["width"],
                height=row["height"],
                upload_timestamp=row["upload_timestamp"],
                download_timestamp=row["download_timestamp"],
                is_excluded=bool(row["is_excluded"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in cursor.fetchall()
        ]

    def search_media_items(self, query_str: str, limit: int = 50) -> list[MediaItem]:
        """Search media items across id, creator, title, caption, and tags."""
        cursor = self.conn.cursor()
        pattern = f"%{query_str}%"
        cursor.execute(
            """
            SELECT DISTINCT m.id, m.source_platform, m.source_id, m.username, m.title, m.caption,
                   m.file_path, m.file_size, m.sha256, m.duration, m.width, m.height,
                   m.upload_timestamp, m.download_timestamp, m.is_excluded, m.created_at,
                   m.updated_at
            FROM media_items m
            LEFT JOIN publishing_metadata p ON m.id = p.media_id
            WHERE m.id LIKE ? OR m.username LIKE ? OR m.title LIKE ?
               OR m.caption LIKE ? OR p.title LIKE ? OR p.tags LIKE ?
            ORDER BY m.created_at DESC
            LIMIT ?
            """,
            (pattern, pattern, pattern, pattern, pattern, pattern, limit),
        )
        return [
            MediaItem(
                id=row["id"],
                source_platform=row["source_platform"],
                source_id=row["source_id"],
                username=row["username"],
                title=row["title"],
                caption=row["caption"],
                file_path=row["file_path"],
                file_size=row["file_size"],
                sha256=row["sha256"],
                duration=row["duration"],
                width=row["width"],
                height=row["height"],
                upload_timestamp=row["upload_timestamp"],
                download_timestamp=row["download_timestamp"],
                is_excluded=bool(row["is_excluded"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in cursor.fetchall()
        ]

    def count_media_items(self, username: Optional[str] = None) -> int:
        """Count total media items in library."""
        cursor = self.conn.cursor()
        if username:
            cursor.execute("SELECT COUNT(*) FROM media_items WHERE username = ?", (username,))
        else:
            cursor.execute("SELECT COUNT(*) FROM media_items")
        return cursor.fetchone()[0]

    def set_media_excluded(self, media_id: str, is_excluded: bool) -> None:
        """Set or unset exclusion from publishing."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                "UPDATE media_items SET is_excluded = ?, updated_at = ? WHERE id = ?",
                (1 if is_excluded else 0, now_iso, media_id),
            )

    # --- Publishing Metadata CRUD ---

    def upsert_publishing_metadata(self, meta: PublishingMetadata) -> None:
        """Insert or update user-editable publishing metadata."""
        now_iso = datetime.now(timezone.utc).isoformat()
        tags_json = json.dumps(meta.tags) if isinstance(meta.tags, list) else meta.tags
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO publishing_metadata (
                    media_id, title, description, tags, category_id, privacy_status,
                    made_for_kids, thumbnail_path, language, rights_confirmed, rights_notes,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(media_id) DO UPDATE SET
                    title = excluded.title,
                    description = excluded.description,
                    tags = excluded.tags,
                    category_id = excluded.category_id,
                    privacy_status = excluded.privacy_status,
                    made_for_kids = excluded.made_for_kids,
                    thumbnail_path = COALESCE(
                        excluded.thumbnail_path, publishing_metadata.thumbnail_path
                    ),
                    language = COALESCE(excluded.language, publishing_metadata.language),
                    rights_confirmed = excluded.rights_confirmed,
                    rights_notes = COALESCE(
                        excluded.rights_notes, publishing_metadata.rights_notes
                    ),
                    updated_at = excluded.updated_at
                """,
                (
                    meta.media_id,
                    meta.title,
                    meta.description,
                    tags_json,
                    meta.category_id,
                    meta.privacy_status,
                    1 if meta.made_for_kids else 0,
                    meta.thumbnail_path,
                    meta.language,
                    1 if meta.rights_confirmed else 0,
                    meta.rights_notes,
                    now_iso,
                ),
            )

    def get_publishing_metadata(self, media_id: str) -> Optional[PublishingMetadata]:
        """Retrieve editable publishing metadata for a media item."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT media_id, title, description, tags, category_id, privacy_status,
                   made_for_kids, thumbnail_path, language, rights_confirmed, rights_notes,
                   updated_at
            FROM publishing_metadata WHERE media_id = ?
            """,
            (media_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        tags = []
        if row["tags"]:
            try:
                tags = json.loads(row["tags"])
            except Exception:
                tags = [t.strip() for t in row["tags"].split(",") if t.strip()]
        return PublishingMetadata(
            media_id=row["media_id"],
            title=row["title"],
            description=row["description"] or "",
            tags=tags,
            category_id=row["category_id"] or "22",
            privacy_status=row["privacy_status"] or "public",
            made_for_kids=bool(row["made_for_kids"]),
            thumbnail_path=row["thumbnail_path"],
            language=row["language"],
            rights_confirmed=bool(row["rights_confirmed"]),
            rights_notes=row["rights_notes"],
            updated_at=row["updated_at"],
        )

    def get_media_metadata(self, media_id: str) -> Optional[MediaMetadata]:
        """Fetch preserved raw source metadata and properties for a media item."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM media_metadata WHERE media_id = ?", (media_id,))
        row = cursor.fetchone()
        if not row:
            return None
        raw_json_str = row["raw_json"] or "{}"
        try:
            tags = json.loads(row["hashtags"]) if row["hashtags"] else []
        except Exception:
            tags = []
        try:
            extra_dict = json.loads(row["extra"]) if row["extra"] else {}
        except Exception:
            extra_dict = {}

        return MediaMetadata(
            media_id=row["media_id"],
            raw_json=json.loads(raw_json_str)
            if raw_json_str.startswith(("{", "["))
            else raw_json_str,
            hashtags=tags,
            audio_info=row["audio_info"],
            thumbnail_url=row["thumbnail_url"],
            thumbnail_path=row["thumbnail_path"],
            extra=extra_dict,
        )

    def upsert_media_metadata(self, meta: MediaMetadata) -> None:
        """Insert or update media metadata record."""
        raw_str = meta.raw_json if isinstance(meta.raw_json, str) else json.dumps(meta.raw_json)
        tags_str = (
            json.dumps(meta.hashtags) if isinstance(meta.hashtags, list) else str(meta.hashtags)
        )
        extra_str = json.dumps(meta.extra) if isinstance(meta.extra, dict) else str(meta.extra)
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO media_metadata (
                    media_id, raw_json, hashtags, audio_info, thumbnail_url, thumbnail_path, extra
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(media_id) DO UPDATE SET
                    raw_json = excluded.raw_json,
                    hashtags = excluded.hashtags,
                    audio_info = excluded.audio_info,
                    thumbnail_url = excluded.thumbnail_url,
                    thumbnail_path = excluded.thumbnail_path,
                    extra = excluded.extra
                """,
                (
                    meta.media_id,
                    raw_str,
                    tags_str,
                    meta.audio_info,
                    meta.thumbnail_url,
                    meta.thumbnail_path,
                    extra_str,
                ),
            )

    # --- Publishing Accounts CRUD ---

    def upsert_publishing_account(self, account: PublishingAccount) -> None:
        """Insert or update a connected publishing account."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO publishing_accounts (
                    id, platform, account_name, channel_id, channel_title,
                    token_storage_key, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    account_name = excluded.account_name,
                    channel_title = excluded.channel_title,
                    token_storage_key = excluded.token_storage_key,
                    is_active = excluded.is_active,
                    updated_at = excluded.updated_at
                """,
                (
                    account.id,
                    account.platform,
                    account.account_name,
                    account.channel_id,
                    account.channel_title,
                    account.token_storage_key,
                    1 if account.is_active else 0,
                    account.created_at or now_iso,
                    now_iso,
                ),
            )

    def get_publishing_account(self, channel_id_or_id: str) -> Optional[PublishingAccount]:
        """Retrieve connected publishing account by channel ID or internal ID."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, platform, account_name, channel_id, channel_title,
                   token_storage_key, is_active, created_at, updated_at
            FROM publishing_accounts WHERE channel_id = ? OR id = ?
            """,
            (channel_id_or_id, channel_id_or_id),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return PublishingAccount(
            id=row["id"],
            platform=row["platform"],
            account_name=row["account_name"],
            channel_id=row["channel_id"],
            channel_title=row["channel_title"],
            token_storage_key=row["token_storage_key"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_all_publishing_accounts(self, active_only: bool = True) -> list[PublishingAccount]:
        """List connected publishing accounts."""
        cursor = self.conn.cursor()
        if active_only:
            cursor.execute(
                "SELECT * FROM publishing_accounts WHERE is_active = 1 ORDER BY channel_title ASC"
            )
        else:
            cursor.execute("SELECT * FROM publishing_accounts ORDER BY channel_title ASC")
        return [
            PublishingAccount(
                id=row["id"],
                platform=row["platform"],
                account_name=row["account_name"],
                channel_id=row["channel_id"],
                channel_title=row["channel_title"],
                token_storage_key=row["token_storage_key"],
                is_active=bool(row["is_active"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in cursor.fetchall()
        ]

    def delete_publishing_account(self, channel_id: str) -> None:
        """Disconnect/delete a publishing account."""
        with self.transaction() as cur:
            cur.execute(
                "DELETE FROM publishing_accounts WHERE channel_id = ? OR id = ?",
                (channel_id, channel_id),
            )

    # --- Publishing Jobs CRUD ---

    def upsert_publishing_job(self, job: PublishingJob) -> None:
        """Insert or update a publishing job record."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO publishing_jobs (
                    id, media_id, channel_id, platform, status, scheduled_publish_at,
                    display_timezone, upload_session_uri, bytes_uploaded, youtube_video_id,
                    youtube_video_url, publication_date, retry_count, last_error, error_type,
                    rights_confirmed, created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    scheduled_publish_at = excluded.scheduled_publish_at,
                    display_timezone = excluded.display_timezone,
                    upload_session_uri = excluded.upload_session_uri,
                    bytes_uploaded = excluded.bytes_uploaded,
                    youtube_video_id = COALESCE(
                        excluded.youtube_video_id, publishing_jobs.youtube_video_id
                    ),
                    youtube_video_url = COALESCE(
                        excluded.youtube_video_url, publishing_jobs.youtube_video_url
                    ),
                    publication_date = COALESCE(
                        excluded.publication_date, publishing_jobs.publication_date
                    ),
                    retry_count = excluded.retry_count,
                    last_error = excluded.last_error,
                    error_type = excluded.error_type,
                    rights_confirmed = excluded.rights_confirmed,
                    updated_at = excluded.updated_at,
                    completed_at = COALESCE(excluded.completed_at, publishing_jobs.completed_at)
                """,
                (
                    job.id,
                    job.media_id,
                    job.channel_id,
                    job.platform,
                    job.status.value,
                    job.scheduled_publish_at,
                    job.display_timezone,
                    job.upload_session_uri,
                    job.bytes_uploaded,
                    job.youtube_video_id,
                    job.youtube_video_url,
                    job.publication_date,
                    job.retry_count,
                    job.last_error,
                    job.error_type,
                    1 if job.rights_confirmed else 0,
                    job.created_at or now_iso,
                    now_iso,
                    job.completed_at,
                ),
            )

    def get_publishing_job(self, job_id: str) -> Optional[PublishingJob]:
        """Fetch publishing job by ID."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM publishing_jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return PublishingJob(
            id=row["id"],
            media_id=row["media_id"],
            channel_id=row["channel_id"],
            platform=row["platform"],
            status=PublishStatus(row["status"]),
            scheduled_publish_at=row["scheduled_publish_at"],
            display_timezone=row["display_timezone"],
            upload_session_uri=row["upload_session_uri"],
            bytes_uploaded=row["bytes_uploaded"],
            youtube_video_id=row["youtube_video_id"],
            youtube_video_url=row["youtube_video_url"],
            publication_date=row["publication_date"],
            retry_count=row["retry_count"],
            last_error=row["last_error"],
            error_type=row["error_type"],
            rights_confirmed=bool(row["rights_confirmed"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )

    def get_publishing_jobs_by_media(self, media_id: str) -> list[PublishingJob]:
        """Find all publishing jobs associated with a media item."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM publishing_jobs WHERE media_id = ? ORDER BY created_at DESC",
            (media_id,),
        )
        return [
            PublishingJob(
                id=row["id"],
                media_id=row["media_id"],
                channel_id=row["channel_id"],
                platform=row["platform"],
                status=PublishStatus(row["status"]),
                scheduled_publish_at=row["scheduled_publish_at"],
                display_timezone=row["display_timezone"],
                upload_session_uri=row["upload_session_uri"],
                bytes_uploaded=row["bytes_uploaded"],
                youtube_video_id=row["youtube_video_id"],
                youtube_video_url=row["youtube_video_url"],
                publication_date=row["publication_date"],
                retry_count=row["retry_count"],
                last_error=row["last_error"],
                error_type=row["error_type"],
                rights_confirmed=bool(row["rights_confirmed"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
            )
            for row in cursor.fetchall()
        ]

    def get_all_publishing_jobs(
        self, status: Optional[PublishStatus] = None, limit: int = 50
    ) -> list[PublishingJob]:
        """Fetch publishing jobs ordered by update time."""
        cursor = self.conn.cursor()
        if status:
            cursor.execute(
                "SELECT * FROM publishing_jobs WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                (status.value, limit),
            )
        else:
            cursor.execute(
                "SELECT * FROM publishing_jobs ORDER BY updated_at DESC LIMIT ?", (limit,)
            )
        return [
            PublishingJob(
                id=row["id"],
                media_id=row["media_id"],
                channel_id=row["channel_id"],
                platform=row["platform"],
                status=PublishStatus(row["status"]),
                scheduled_publish_at=row["scheduled_publish_at"],
                display_timezone=row["display_timezone"],
                upload_session_uri=row["upload_session_uri"],
                bytes_uploaded=row["bytes_uploaded"],
                youtube_video_id=row["youtube_video_id"],
                youtube_video_url=row["youtube_video_url"],
                publication_date=row["publication_date"],
                retry_count=row["retry_count"],
                last_error=row["last_error"],
                error_type=row["error_type"],
                rights_confirmed=bool(row["rights_confirmed"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
            )
            for row in cursor.fetchall()
        ]

    def update_job_status(
        self,
        job_id: str,
        status: PublishStatus,
        error: Optional[str] = None,
        error_type: Optional[str] = None,
    ) -> None:
        """Update job lifecycle status."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE publishing_jobs
                SET status = ?, last_error = ?, error_type = ?, updated_at = ?
                WHERE id = ?
                """,
                (status.value, error, error_type, now_iso, job_id),
            )

    def mark_job_published(
        self, job_id: str, video_id: str, video_url: str, publication_date: Optional[str] = None
    ) -> None:
        """Atomically record successful upload / publication."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE publishing_jobs
                SET status = 'PUBLISHED',
                    youtube_video_id = ?,
                    youtube_video_url = ?,
                    publication_date = ?,
                    last_error = NULL,
                    updated_at = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (video_id, video_url, publication_date or now_iso, now_iso, now_iso, job_id),
            )

    # --- Resumable Upload Sessions CRUD ---

    def save_upload_session(self, session: UploadSession) -> None:
        """Save resumable upload session URI and byte progress."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO upload_sessions (
                    session_uri, job_id, file_path, file_size, bytes_uploaded,
                    last_chunk_byte, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_uri) DO UPDATE SET
                    bytes_uploaded = excluded.bytes_uploaded,
                    last_chunk_byte = excluded.last_chunk_byte,
                    updated_at = excluded.updated_at
                """,
                (
                    session.session_uri,
                    session.job_id,
                    session.file_path,
                    session.file_size,
                    session.bytes_uploaded,
                    session.last_chunk_byte,
                    session.created_at or now_iso,
                    now_iso,
                ),
            )

    def get_upload_session(self, job_id: str) -> Optional[UploadSession]:
        """Fetch active upload session for a job."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM upload_sessions
            WHERE job_id = ? ORDER BY updated_at DESC LIMIT 1
            """,
            (job_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return UploadSession(
            session_uri=row["session_uri"],
            job_id=row["job_id"],
            file_path=row["file_path"],
            file_size=row["file_size"],
            bytes_uploaded=row["bytes_uploaded"],
            last_chunk_byte=row["last_chunk_byte"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def delete_upload_session(self, job_id: str) -> None:
        """Delete finished or expired upload session."""
        with self.transaction() as cur:
            cur.execute("DELETE FROM upload_sessions WHERE job_id = ?", (job_id,))

    # --- Scheduling CRUD ---

    def upsert_schedule_entry(self, entry: ScheduleEntry) -> None:
        """Record or update a scheduled publication calendar slot."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO schedule_entries (
                    id, job_id, media_id, channel_id, scheduled_utc, local_display_time,
                    timezone_name, interval_spec, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    scheduled_utc = excluded.scheduled_utc,
                    local_display_time = excluded.local_display_time,
                    timezone_name = excluded.timezone_name,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    entry.id,
                    entry.job_id,
                    entry.media_id,
                    entry.channel_id,
                    entry.scheduled_utc,
                    entry.local_display_time,
                    entry.timezone_name,
                    entry.interval_spec,
                    entry.status.value,
                    entry.created_at or now_iso,
                    now_iso,
                ),
            )

    def get_all_schedules(
        self,
        channel_id: Optional[str] = None,
        status: Optional[PublishStatus] = None,
        limit: int = 100,
    ) -> list[ScheduleEntry]:
        """Fetch schedule entries sorted chronologically."""
        cursor = self.conn.cursor()
        conditions = []
        params: list = []
        if channel_id:
            conditions.append("channel_id = ?")
            params.append(channel_id)
        if status:
            conditions.append("status = ?")
            params.append(status.value)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM schedule_entries {where} ORDER BY scheduled_utc ASC LIMIT ?"
        params.append(limit)
        cursor.execute(query, tuple(params))
        return [
            ScheduleEntry(
                id=row["id"],
                job_id=row["job_id"],
                media_id=row["media_id"],
                channel_id=row["channel_id"],
                scheduled_utc=row["scheduled_utc"],
                local_display_time=row["local_display_time"],
                timezone_name=row["timezone_name"],
                interval_spec=row["interval_spec"],
                status=PublishStatus(row["status"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in cursor.fetchall()
        ]

    def get_schedule_by_job(self, job_id: str) -> Optional[ScheduleEntry]:
        """Get schedule slot by job ID."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM schedule_entries WHERE job_id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return ScheduleEntry(
            id=row["id"],
            job_id=row["job_id"],
            media_id=row["media_id"],
            channel_id=row["channel_id"],
            scheduled_utc=row["scheduled_utc"],
            local_display_time=row["local_display_time"],
            timezone_name=row["timezone_name"],
            interval_spec=row["interval_spec"],
            status=PublishStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_schedule_by_media(self, media_id: str) -> Optional[ScheduleEntry]:
        """Get schedule slot by media ID."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM schedule_entries
            WHERE media_id = ? ORDER BY scheduled_utc DESC LIMIT 1
            """,
            (media_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return ScheduleEntry(
            id=row["id"],
            job_id=row["job_id"],
            media_id=row["media_id"],
            channel_id=row["channel_id"],
            scheduled_utc=row["scheduled_utc"],
            local_display_time=row["local_display_time"],
            timezone_name=row["timezone_name"],
            interval_spec=row["interval_spec"],
            status=PublishStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def cancel_schedule(self, entry_id_or_job_id: str) -> bool:
        """Cancel a pending schedule slot."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE schedule_entries
                SET status = 'CANCELLED', updated_at = ?
                WHERE id = ? OR job_id = ?
                """,
                (now_iso, entry_id_or_job_id, entry_id_or_job_id),
            )
            cur.execute(
                """
                UPDATE publishing_jobs
                SET status = 'CANCELLED', updated_at = ?
                WHERE id = ?
                """,
                (now_iso, entry_id_or_job_id),
            )
            return cur.rowcount > 0

    # --- Quota Management CRUD ---

    def get_today_quota(self, date_utc: Optional[str] = None) -> QuotaRecord:
        """Fetch or initialize today's quota usage record."""
        target_date = date_utc or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM quota_usage WHERE date_utc = ?", (target_date,))
        row = cursor.fetchone()
        if row:
            return QuotaRecord(
                date_utc=row["date_utc"],
                upload_units_used=row["upload_units_used"],
                api_requests_count=row["api_requests_count"],
                daily_limit=row["daily_limit"],
                last_request_time=row["last_request_time"],
            )
        # Create new default record for today
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO quota_usage (
                    date_utc, upload_units_used, api_requests_count, daily_limit
                ) VALUES (?, 0, 0, 100)
                """,
                (target_date,),
            )
        return QuotaRecord(
            date_utc=target_date, upload_units_used=0, api_requests_count=0, daily_limit=100
        )

    def record_quota_usage(
        self, upload_units: int = 1, api_requests: int = 1, date_utc: Optional[str] = None
    ) -> QuotaRecord:
        """Atomically record consumed quota units."""
        target_date = date_utc or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO quota_usage (
                    date_utc, upload_units_used, api_requests_count, daily_limit, last_request_time
                ) VALUES (?, ?, ?, 100, ?)
                ON CONFLICT(date_utc) DO UPDATE SET
                    upload_units_used = upload_units_used + excluded.upload_units_used,
                    api_requests_count = api_requests_count + excluded.api_requests_count,
                    last_request_time = excluded.last_request_time
                """,
                (target_date, upload_units, api_requests, now_iso),
            )
        return self.get_today_quota(target_date)

    def close(self) -> None:
        """Close SQLite connection cleanly."""
        try:
            self.conn.close()
        except Exception:
            pass
