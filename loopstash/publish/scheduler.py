"""YouTube scheduling engine, publication calendar generation, and future public release."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Optional

from loopstash.database import Database
from loopstash.models import (
    PublishingJob,
    PublishingMetadata,
    PublishStatus,
    ScheduleEntry,
)
from loopstash.publish.oauth import YouTubeAuthManager
from loopstash.publish.quota import QuotaManager
from loopstash.publish.uploader import YouTubeUploader
from loopstash.utils.timezones import (
    generate_schedule_slots,
    parse_schedule_datetime,
    to_utc_iso,
    validate_future_datetime,
)

logger = logging.getLogger(__name__)


class YouTubeScheduler:
    """Manages timezone-aware schedule slot creation and private YouTube upload with publishAt."""

    def __init__(
        self,
        db: Database,
        uploader: Optional[YouTubeUploader] = None,
        auth_manager: Optional[YouTubeAuthManager] = None,
        quota_manager: Optional[QuotaManager] = None,
    ):
        self.db = db
        self.auth_manager = auth_manager or YouTubeAuthManager(db)
        self.quota_manager = quota_manager or QuotaManager(db)
        self.uploader = uploader or YouTubeUploader(
            db=db,
            auth_manager=self.auth_manager,
            quota_manager=self.quota_manager,
        )

    def preview_schedule(
        self,
        start_date: str,
        start_time: str,
        timezone_str: str,
        count: int,
        interval: str | int = "daily",
    ) -> list[dict[str, Any]]:
        """Generate and preview the publication calendar without creating database records."""
        return generate_schedule_slots(
            start_date=start_date,
            start_time=start_time,
            tz_name=timezone_str,
            count=count,
            interval=interval,
        )

    def schedule_batch(
        self,
        media_ids: list[str],
        channel_id: str,
        start_date: str,
        start_time: str,
        timezone_str: str,
        interval: str | int = "daily",
        confirm_rights: bool = False,
        progress_callback: Optional[Callable[[str, int, int, float], None]] = None,
    ) -> list[dict[str, Any]]:
        """Schedule media assets for future public release using YouTube's status.publishAt."""
        if not media_ids:
            return []

        account = self.db.get_publishing_account(channel_id)
        if not account or not account.is_active:
            raise KeyError(
                f"Publishing account '{channel_id}' not found. "
                "Run 'loopstash publish auth youtube' first."
            )

        slots = generate_schedule_slots(
            start_date=start_date,
            start_time=start_time,
            tz_name=timezone_str,
            count=len(media_ids),
            interval=interval,
        )

        service = self.auth_manager.build_service(channel_id)
        results: list[dict[str, Any]] = []

        for idx, mid in enumerate(media_ids):
            slot = slots[idx]
            item = self.db.get_media_item(mid)
            if not item:
                results.append({"media_id": mid, "status": "FAILED", "error": "Item not found"})
                continue

            pub_meta = self.db.get_publishing_metadata(mid)
            if not pub_meta:
                pub_meta = PublishingMetadata(
                    media_id=mid,
                    title=item.title or f"Media {mid}",
                    description=item.caption or "",
                    tags=[],
                    privacy_status="private",
                )

            if confirm_rights:
                pub_meta.rights_confirmed = True
                self.db.upsert_publishing_metadata(pub_meta)

            if not pub_meta.rights_confirmed:
                results.append(
                    {
                        "media_id": mid,
                        "status": "NEEDS_ATTENTION",
                        "error": "Publishing rights not confirmed.",
                    }
                )
                continue

            pub_meta.privacy_status = "private"

            job_id = f"sched_{uuid.uuid4().hex[:12]}"
            sched_entry_id = f"slot_{uuid.uuid4().hex[:12]}"

            job = PublishingJob(
                id=job_id,
                media_id=mid,
                channel_id=channel_id,
                platform="youtube",
                status=PublishStatus.QUEUED,
                scheduled_publish_at=slot["utc_iso"],
                display_timezone=timezone_str,
                rights_confirmed=True,
            )
            self.db.upsert_publishing_job(job)

            entry = ScheduleEntry(
                id=sched_entry_id,
                job_id=job_id,
                media_id=mid,
                channel_id=channel_id,
                scheduled_utc=slot["utc_iso"],
                local_display_time=slot["local_display"],
                timezone_name=timezone_str,
                interval_spec=str(interval),
                status=PublishStatus.SCHEDULED,
            )
            self.db.upsert_schedule_entry(entry)

            current_mid = mid

            def make_chunk_cb(target_mid: str):
                def _cb(bytes_sent: int, total_bytes: int, speed: float) -> None:
                    if progress_callback:
                        progress_callback(target_mid, bytes_sent, total_bytes, speed)

                return _cb

            try:
                video_id, video_url = self.uploader.upload_media(
                    job=job,
                    media_item=item,
                    metadata=pub_meta,
                    youtube_service=service,
                    progress_callback=make_chunk_cb(current_mid),
                )
                entry.status = PublishStatus.SCHEDULED
                self.db.upsert_schedule_entry(entry)

                results.append(
                    {
                        "media_id": mid,
                        "status": "SCHEDULED",
                        "youtube_video_id": video_id,
                        "youtube_video_url": video_url,
                        "scheduled_utc": slot["utc_iso"],
                        "local_display": slot["local_display"],
                        "job_id": job_id,
                    }
                )
            except Exception as e:
                logger.error("Failed scheduling media '%s': %s", mid, e)
                results.append(
                    {
                        "media_id": mid,
                        "status": "FAILED",
                        "error": str(e),
                        "job_id": job_id,
                    }
                )

        return results

    def _find_job(self, identifier: str) -> Optional[PublishingJob]:
        """Find publishing job by job ID, YouTube video ID, schedule entry ID, or media ID."""
        # 1. By job ID
        job = self.db.get_publishing_job(identifier)
        if job:
            return job

        # 2. By YouTube video ID
        job = self.db.get_publishing_job_by_youtube_id(identifier)
        if job:
            return job

        # 3. By schedule entry ID
        entry = self.db.get_schedule_entry(identifier)
        if entry:
            job = self.db.get_publishing_job(entry.job_id)
            if job:
                return job

        # 4. By media ID (publishing jobs)
        jobs = self.db.get_publishing_jobs_by_media(identifier)
        if jobs:
            return jobs[0]

        # 5. By media ID (schedule entries)
        sched_entry = self.db.get_schedule_by_media(identifier)
        if sched_entry:
            job = self.db.get_publishing_job(sched_entry.job_id)
            if job:
                return job

        return None

    def reschedule_video(
        self,
        job_or_media_id: str,
        new_date: str,
        new_time: str,
        timezone_str: str,
    ) -> bool:
        """Update publication slot time and inform YouTube Data API."""
        job = self._find_job(job_or_media_id)
        if not job:
            raise KeyError(f"Publishing record '{job_or_media_id}' not found.")

        new_dt = parse_schedule_datetime(new_date, new_time, timezone_str)
        is_valid, err = validate_future_datetime(new_dt)
        if not is_valid:
            raise ValueError(f"New scheduled time is invalid: {err}")

        new_utc_iso = to_utc_iso(new_dt)
        local_display = f"{new_dt.strftime('%Y-%m-%d %H:%M:%S')} ({timezone_str})"

        # If already uploaded to YouTube, update remote video resource
        if job.youtube_video_id:
            service = self.auth_manager.build_service(job.channel_id)
            body = {
                "id": job.youtube_video_id,
                "status": {
                    "privacyStatus": "private",
                    "publishAt": new_utc_iso,
                },
            }
            try:
                service.videos().update(part="status", body=body).execute()
            except Exception as e:
                raise RuntimeError(f"YouTube rejected rescheduling request: {e}") from e

        # Update local database
        job.scheduled_publish_at = new_utc_iso
        job.display_timezone = timezone_str
        self.db.upsert_publishing_job(job)

        entry = self.db.get_schedule_by_job(job.id)
        if entry:
            entry.scheduled_utc = new_utc_iso
            entry.local_display_time = local_display
            entry.timezone_name = timezone_str
            self.db.upsert_schedule_entry(entry)

        return True

    def cancel_schedule(self, job_or_media_id: str) -> bool:
        """Cancel scheduled release in remote YouTube API and local database."""
        job = self._find_job(job_or_media_id)
        if not job:
            return False

        # If already uploaded to YouTube, update remote video resource to cancel scheduled publish
        if job.youtube_video_id:
            service = self.auth_manager.build_service(job.channel_id)
            body = {
                "id": job.youtube_video_id,
                "status": {
                    "privacyStatus": "private",
                },
            }
            try:
                service.videos().update(part="status", body=body).execute()
            except Exception as e:
                raise RuntimeError(f"YouTube rejected cancellation request: {e}") from e

        return self.db.cancel_schedule(job.id)
