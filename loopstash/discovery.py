"""Profile discovery and Reel cataloging for LOOPSTASH."""

from __future__ import annotations

import logging
from typing import Optional

from loopstash.backends.base import BaseBackend
from loopstash.database import Database
from loopstash.duplicate import DuplicateDetector
from loopstash.models import DownloadStatus, JobRecord, ReelItem
from loopstash.utils.validation import validate_instagram_url

logger = logging.getLogger(__name__)


class DiscoveryEngine:
    """Discovers Reels from Instagram profiles and registers them in the database."""

    def __init__(self, backend: BaseBackend, db: Database):
        self.backend = backend
        self.db = db
        self.duplicate_detector = DuplicateDetector(db)

    def discover_profile(
        self,
        profile_input: str,
        browser: Optional[str] = None,
        force: bool = False,
        dry_run: bool = False,
        max_items: Optional[int] = None,
    ) -> tuple[str, str, list[ReelItem], int]:
        """Validate profile and extract all accessible Reels.

        Returns:
            (username, profile_url, discovered_reels, duplicates_count)
        """
        is_valid, username, profile_url, err = validate_instagram_url(profile_input)
        if not is_valid or not username or not profile_url:
            raise ValueError(err or f"Invalid Instagram profile input: '{profile_input}'")

        if not dry_run:
            # Register profile in database
            self.db.upsert_profile(username, profile_url)

        # Call backend to extract reels
        raw_reels = self.backend.extract_reels(
            username=username,
            profile_url=profile_url,
            browser=browser,
            max_items=max_items,
        )

        duplicates_count = 0
        discovered: list[ReelItem] = []

        for reel in raw_reels:
            is_dup, existing_record, _ = self.duplicate_detector.check_reel_duplicate(
                reel.shortcode, force=force
            )

            if is_dup and not force:
                duplicates_count += 1
                if not dry_run and not existing_record:
                    job = JobRecord(
                        shortcode=reel.shortcode,
                        username=username,
                        profile_url=profile_url,
                        media_id=reel.media_id,
                        reel_url=reel.reel_url,
                        status=DownloadStatus.SKIPPED_DUPLICATE,
                        upload_timestamp=reel.upload_timestamp,
                        backend=self.backend.name,
                    )
                    self.db.upsert_reel(job, force=force)
                continue

            if not dry_run:
                # Queue as PENDING in database
                job = JobRecord(
                    shortcode=reel.shortcode,
                    username=username,
                    profile_url=profile_url,
                    media_id=reel.media_id,
                    reel_url=reel.reel_url,
                    status=DownloadStatus.PENDING,
                    upload_timestamp=reel.upload_timestamp,
                    backend=self.backend.name,
                )
                self.db.upsert_reel(job, force=force)
            discovered.append(reel)

        if not dry_run:
            self.db.update_profile_sync(username, len(raw_reels))
        return username, profile_url, discovered, duplicates_count
