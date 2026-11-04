"""Core download coordinator and resume orchestrator for LOOPSTASH."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from loopstash.backends.base import (
    AuthenticationRequiredError,
    BaseBackend,
    RateLimitError,
)
from loopstash.database import Database
from loopstash.discovery import DiscoveryEngine
from loopstash.duplicate import DuplicateDetector
from loopstash.models import DownloadProgress, DownloadStatus, JobRecord, ReelItem
from loopstash.retry import RetryPolicy
from loopstash.storage import StorageManager

logger = logging.getLogger(__name__)


@dataclass
class DownloadSummary:
    """Aggregated outcome of a download or resume operation."""

    username: str
    total_discovered: int = 0
    downloaded: int = 0
    skipped_duplicates: int = 0
    failed: int = 0
    total_bytes: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)  # (shortcode, error_message)
    is_dry_run: bool = False


class Downloader:
    """Orchestrates Reel discovery, bounded retries, atomic finalization, and persistence."""

    def __init__(
        self,
        backend: BaseBackend,
        storage: StorageManager,
        db: Database,
        retry_policy: Optional[RetryPolicy] = None,
        rate_limit_pause: float = 2.0,
        min_free_space_mb: int = 500,
    ):
        self.backend = backend
        self.storage = storage
        self.db = db
        self.retry_policy = retry_policy or RetryPolicy()
        self.duplicate_detector = DuplicateDetector(db)
        self.discovery_engine = DiscoveryEngine(backend, db)
        self.rate_limit_pause = rate_limit_pause
        self.min_free_space_mb = min_free_space_mb

    def _ensure_disk_space(self) -> None:
        """Validate sufficient free space before initiating downloads."""
        has_space, free_mb = self.storage.check_disk_space(self.min_free_space_mb)
        if not has_space:
            raise OSError(
                f"Insufficient disk space at destination '{self.storage.base_dir}'. "
                f"Available: {free_mb} MB, Required: {self.min_free_space_mb} MB."
            )

    def download_profile(
        self,
        profile_input: str,
        browser: Optional[str] = None,
        force: bool = False,
        dry_run: bool = False,
        max_items: Optional[int] = None,
        progress_callback: Optional[Callable[[DownloadProgress], None]] = None,
    ) -> DownloadSummary:
        """Discover and download all accessible Reels for a profile."""
        self._ensure_disk_space()

        username, profile_url, to_download, duplicates_count = (
            self.discovery_engine.discover_profile(
                profile_input=profile_input,
                browser=browser,
                force=force,
                max_items=max_items,
            )
        )

        total_discovered = len(to_download) + duplicates_count
        summary = DownloadSummary(
            username=username,
            total_discovered=total_discovered,
            skipped_duplicates=duplicates_count,
            is_dry_run=dry_run,
        )

        if dry_run:
            logger.info("Dry-run requested: skipping physical downloads.")
            return summary

        # Download pending reels
        self._download_batch(
            reels=to_download,
            username=username,
            profile_url=profile_url,
            summary=summary,
            browser=browser,
            progress_callback=progress_callback,
        )

        return summary

    def resume(
        self,
        username: Optional[str] = None,
        browser: Optional[str] = None,
        progress_callback: Optional[Callable[[DownloadProgress], None]] = None,
    ) -> list[DownloadSummary]:
        """Resume any unfinished or failed downloads."""
        self._ensure_disk_space()
        self.db.reconcile_stale_jobs()

        resumable_jobs = self.db.get_resumable_reels(username=username)
        if not resumable_jobs:
            return []

        # Group by username
        jobs_by_user: dict[str, list[JobRecord]] = {}
        for job in resumable_jobs:
            jobs_by_user.setdefault(job.username, []).append(job)

        summaries = []
        for uname, jobs in jobs_by_user.items():
            profile_url = jobs[0].profile_url
            summary = DownloadSummary(
                username=uname,
                total_discovered=len(jobs),
            )
            # Convert JobRecord to ReelItem
            reels_to_download = [
                ReelItem(
                    shortcode=j.shortcode,
                    username=j.username,
                    reel_url=j.reel_url,
                    media_id=j.media_id,
                    profile_url=j.profile_url,
                    upload_timestamp=j.upload_timestamp,
                )
                for j in jobs
            ]

            self._download_batch(
                reels=reels_to_download,
                username=uname,
                profile_url=profile_url,
                summary=summary,
                browser=browser,
                progress_callback=progress_callback,
            )
            summaries.append(summary)

        return summaries

    def _download_batch(
        self,
        reels: list[ReelItem],
        username: str,
        profile_url: str,
        summary: DownloadSummary,
        browser: Optional[str] = None,
        progress_callback: Optional[Callable[[DownloadProgress], None]] = None,
    ) -> None:
        """Download a sequence of Reels with error handling and status updates."""
        total_items = len(reels)
        remaining = total_items

        for idx, reel in enumerate(reels, start=1):
            temp_staging = self.storage.prepare_temp_download_path(reel)
            target_path = self.storage.get_target_file_path(reel)

            self.db.mark_downloading(reel.shortcode)

            if progress_callback:
                progress_callback(
                    DownloadProgress(
                        shortcode=reel.shortcode,
                        status=DownloadStatus.DOWNLOADING,
                        total_reels=summary.total_discovered,
                        completed_reels=summary.downloaded,
                        duplicate_reels=summary.skipped_duplicates,
                        failed_reels=summary.failed,
                        remaining_reels=remaining,
                        current_file=f"{reel.shortcode}.mp4",
                        message=f"Downloading reel {idx}/{total_items}...",
                    )
                )

            try:
                # Execute download with bounded retries
                downloaded_file = self.retry_policy.execute(
                    self.backend.download_reel,
                    reel=reel,
                    target_file=target_path,
                    temp_file=temp_staging,
                    browser=browser,
                )

                # Finalize atomic move, compute SHA-256, write metadata
                final_path, file_size, sha256 = self.storage.finalize_reel(
                    reel=reel,
                    downloaded_temp_file=downloaded_file,
                )

                # Mark completed in persistent database
                self.db.mark_completed(
                    shortcode=reel.shortcode,
                    destination_path=str(final_path),
                    file_size=file_size,
                    sha256_hash=sha256,
                )

                summary.downloaded += 1
                summary.total_bytes += file_size
                remaining -= 1

                if progress_callback:
                    progress_callback(
                        DownloadProgress(
                            shortcode=reel.shortcode,
                            status=DownloadStatus.COMPLETED,
                            total_reels=summary.total_discovered,
                            completed_reels=summary.downloaded,
                            duplicate_reels=summary.skipped_duplicates,
                            failed_reels=summary.failed,
                            remaining_reels=remaining,
                            current_file=final_path.name,
                            message=f"Saved {final_path.name} ({file_size // 1024} KB)",
                        )
                    )

                # Rate limit pause between media items
                if self.rate_limit_pause > 0 and idx < total_items:
                    time.sleep(self.rate_limit_pause)

            except (AuthenticationRequiredError, RateLimitError) as auth_err:
                # Fatal restrictions: stop batch immediately to prevent IP ban
                err_msg = str(auth_err)
                self.db.mark_failed(reel.shortcode, err_msg)
                summary.failed += 1
                summary.errors.append((reel.shortcode, err_msg))
                temp_staging.unlink(missing_ok=True)
                logger.error("Halting download batch for @%s: %s", username, err_msg)
                break

            except Exception as e:
                err_msg = str(e)
                self.db.mark_failed(reel.shortcode, err_msg)
                summary.failed += 1
                summary.errors.append((reel.shortcode, err_msg))
                temp_staging.unlink(missing_ok=True)
                remaining -= 1

                if progress_callback:
                    progress_callback(
                        DownloadProgress(
                            shortcode=reel.shortcode,
                            status=DownloadStatus.FAILED,
                            total_reels=summary.total_discovered,
                            completed_reels=summary.downloaded,
                            duplicate_reels=summary.skipped_duplicates,
                            failed_reels=summary.failed,
                            remaining_reels=remaining,
                            current_file=f"{reel.shortcode}.mp4",
                            message=f"Failed: {err_msg}",
                        )
                    )

        # Update manifest.json
        completed_reels = self.db.get_reels_for_profile(username, status=DownloadStatus.COMPLETED)
        total_archived_bytes = sum(r.file_size or 0 for r in completed_reels)
        self.storage.update_profile_manifest(
            username=username,
            profile_url=profile_url,
            archived_count=len(completed_reels),
            total_size_bytes=total_archived_bytes,
        )
