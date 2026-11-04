"""Publishing queue management, batch preview, and immediate upload orchestration."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from loopstash.database import Database
from loopstash.models import PublishingJob, PublishingMetadata, PublishStatus
from loopstash.publish.oauth import YouTubeAuthManager
from loopstash.publish.quota import QuotaManager
from loopstash.publish.uploader import YouTubeUploader

logger = logging.getLogger(__name__)


class PublishingQueue:
    """Coordinates batch preflight checks, rights enforcement, and immediate public publishing."""

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

    def preview_batch(
        self,
        media_ids: list[str],
        channel_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Generate comprehensive preflight preview before initiating uploads."""
        channel_title = "Unselected"
        if channel_id:
            acc = self.db.get_publishing_account(channel_id)
            if acc:
                channel_title = acc.channel_title

        total_bytes = 0
        valid_items: list[dict[str, Any]] = []
        issues: list[dict[str, str]] = []
        already_published: list[str] = []

        for mid in media_ids:
            item = self.db.get_media_item(mid)
            if not item:
                issues.append({"media_id": mid, "error": "Media item not found in database"})
                continue

            if not item.file_path or not Path(item.file_path).is_file():
                issues.append({"media_id": mid, "error": f"File missing on disk: {item.file_path}"})
                continue

            # Check if previously published
            prior_jobs = self.db.get_publishing_jobs_by_media(mid)
            published_job = next(
                (j for j in prior_jobs if j.status == PublishStatus.PUBLISHED), None
            )
            if published_job:
                already_published.append(mid)

            pub_meta = self.db.get_publishing_metadata(mid)
            if pub_meta and pub_meta.title:
                title = pub_meta.title
            else:
                title = item.title or f"Media {mid}"
            rights_ok = bool(pub_meta and pub_meta.rights_confirmed)

            f_size = item.file_size or Path(item.file_path).stat().st_size
            total_bytes += f_size

            valid_items.append(
                {
                    "media_id": mid,
                    "title": title[:100],
                    "file_path": item.file_path,
                    "file_size": f_size,
                    "rights_confirmed": rights_ok,
                    "already_published": published_job is not None,
                    "youtube_id": published_job.youtube_video_id if published_job else None,
                }
            )

        quota_status = self.quota_manager.format_quota_status()

        return {
            "total_count": len(valid_items),
            "total_bytes": total_bytes,
            "channel_title": channel_title,
            "items": valid_items,
            "issues": issues,
            "already_published_count": len(already_published),
            "quota_status": quota_status,
        }

    def publish_now(
        self,
        media_ids: list[str],
        channel_id: str,
        confirm_rights: bool = False,
        progress_callback: Optional[Callable[[str, int, int, float], None]] = None,
    ) -> list[dict[str, Any]]:
        """Execute immediate public publishing for specified media assets."""
        account = self.db.get_publishing_account(channel_id)
        if not account or not account.is_active:
            raise KeyError(
                f"Publishing account '{channel_id}' not found. "
                "Run 'loopstash publish auth youtube' first."
            )

        # Quota check
        allowed, used, limit = self.quota_manager.check_upload_quota(units_needed=len(media_ids))
        if not allowed:
            raise RuntimeError(
                f"Requested batch of {len(media_ids)} uploads exceeds remaining daily quota "
                f"({used}/{limit} units used). Pause or reduce batch size."
            )

        service = self.auth_manager.build_service(channel_id)
        results: list[dict[str, Any]] = []

        for mid in media_ids:
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
                    privacy_status="public",
                )

            # Apply batch rights confirmation if provided
            if confirm_rights:
                pub_meta.rights_confirmed = True
                self.db.upsert_publishing_metadata(pub_meta)

            if not pub_meta.rights_confirmed:
                results.append(
                    {
                        "media_id": mid,
                        "status": "NEEDS_ATTENTION",
                        "error": "Publishing rights not confirmed (permission required).",
                    }
                )
                continue

            # Ensure privacy status is explicitly public
            pub_meta.privacy_status = "public"

            job_id = f"pub_{uuid.uuid4().hex[:12]}"
            job = PublishingJob(
                id=job_id,
                media_id=mid,
                channel_id=channel_id,
                platform="youtube",
                status=PublishStatus.QUEUED,
                rights_confirmed=True,
            )
            self.db.upsert_publishing_job(job)

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
                results.append(
                    {
                        "media_id": mid,
                        "status": "PUBLISHED",
                        "youtube_video_id": video_id,
                        "youtube_video_url": video_url,
                        "job_id": job_id,
                    }
                )
            except Exception as e:
                logger.error("Failed uploading media '%s': %s", mid, e)
                results.append(
                    {
                        "media_id": mid,
                        "status": "FAILED",
                        "error": str(e),
                        "job_id": job_id,
                    }
                )

        return results
