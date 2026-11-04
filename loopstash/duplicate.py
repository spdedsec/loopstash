"""Duplicate detection and archive verification for LOOPSTASH."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from loopstash.database import Database
from loopstash.models import DownloadStatus, JobRecord
from loopstash.utils.hashing import compute_file_sha256

logger = logging.getLogger(__name__)


class DuplicateDetector:
    """Handles Reel-level and file SHA-256 level duplicate identification."""

    def __init__(self, db: Database):
        self.db = db

    def check_reel_duplicate(
        self, shortcode: str, force: bool = False
    ) -> tuple[bool, Optional[JobRecord], str]:
        """Check whether a Reel has already been downloaded.

        Returns: (is_duplicate, existing_record, reason)
        """
        if force:
            return False, None, "Forced redownload requested by user (--force)."

        record = self.db.get_reel(shortcode)
        if not record:
            return False, None, "New Reel not previously seen."

        if record.status == DownloadStatus.COMPLETED:
            if record.destination_path:
                dest = Path(record.destination_path)
                if dest.is_file() and dest.stat().st_size > 0:
                    return (
                        True,
                        record,
                        f"Already completed and verified on disk ({dest.name}).",
                    )
                else:
                    logger.warning(
                        "Reel %s marked COMPLETED in database but missing from %s. Re-downloading.",
                        shortcode,
                        dest,
                    )
                    return False, record, "File missing from disk despite completed record."
            return True, record, "Completed in database."

        if record.status == DownloadStatus.SKIPPED_DUPLICATE:
            if record.destination_path:
                dest = Path(record.destination_path)
                if not (dest.is_file() and dest.stat().st_size > 0):
                    return False, record, "File missing from disk despite duplicate record."
            return True, record, "Previously skipped as duplicate."

        return False, record, f"Reel in state {record.status.value}; eligible for download."

    def check_hash_duplicate(self, sha256_hash: str) -> Optional[dict]:
        """Check if an identical media file exists in the database by SHA-256."""
        return self.db.get_reel_by_hash(sha256_hash)

    def verify_archive(self, username: Optional[str] = None) -> dict:
        """Verify on-disk archive integrity for all COMPLETED reels.

        Checks:
        1. File existence on disk
        2. Non-zero file size
        3. Matching SHA-256 hash against database record
        """
        if username:
            records = self.db.get_reels_for_profile(username, status=DownloadStatus.COMPLETED)
        else:
            records = self.db.get_all_completed_reels()

        verified = 0
        missing = 0
        corrupted = 0
        details = []

        for r in records:
            if not r.destination_path:
                missing += 1
                details.append((r.shortcode, "MISSING", "No destination path recorded"))
                continue

            file_path = Path(r.destination_path)
            if not file_path.is_file():
                missing += 1
                details.append((r.shortcode, "MISSING", f"File not found: {file_path}"))
                continue

            actual_size = file_path.stat().st_size
            if actual_size == 0:
                corrupted += 1
                details.append((r.shortcode, "CORRUPTED", f"Empty file (0 bytes): {file_path}"))
                continue

            if r.sha256_hash:
                actual_hash = compute_file_sha256(file_path)
                if actual_hash != r.sha256_hash:
                    corrupted += 1
                    stored_short = r.sha256_hash[:8]
                    actual_short = actual_hash[:8]
                    details.append(
                        (
                            r.shortcode,
                            "CORRUPTED",
                            f"Hash mismatch (stored: {stored_short}..., actual: {actual_short}...)",
                        )
                    )
                    continue

            verified += 1
            details.append((r.shortcode, "OK", str(file_path)))

        return {
            "total_checked": len(records),
            "verified": verified,
            "missing": missing,
            "corrupted": corrupted,
            "details": details,
        }
