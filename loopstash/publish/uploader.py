"""YouTube resumable video upload engine with session recovery and quota enforcement."""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import Any, Callable, Optional

import requests
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from loopstash.database import Database
from loopstash.models import (
    MediaItem,
    PublishingJob,
    PublishingMetadata,
    PublishStatus,
    UploadSession,
)
from loopstash.publish.oauth import YouTubeAuthManager
from loopstash.publish.quota import QuotaManager
from loopstash.utils.hashing import compute_file_sha256

logger = logging.getLogger(__name__)

CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB chunk size for resumable uploads
MAX_UPLOAD_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 2.0


class YouTubeUploader:
    """Coordinates chunked resumable transfers, session persistence, and status reconciliation."""

    def __init__(
        self,
        db: Database,
        auth_manager: Optional[YouTubeAuthManager] = None,
        quota_manager: Optional[QuotaManager] = None,
    ):
        self.db = db
        self.auth_manager = auth_manager or YouTubeAuthManager(db)
        self.quota_manager = quota_manager or QuotaManager(db)

    def upload_media(
        self,
        job: PublishingJob,
        media_item: MediaItem,
        metadata: PublishingMetadata,
        youtube_service: Optional[Resource] = None,
        progress_callback: Optional[Callable[[int, int, float], None]] = None,
    ) -> tuple[str, str]:
        """Upload a video asset using official resumable uploading.

        Returns (youtube_video_id, youtube_video_url).
        """
        # Section 13 Safeguard: Strict copyright and rights confirmation
        if not metadata.rights_confirmed and not job.rights_confirmed:
            self.db.update_job_status(
                job.id,
                PublishStatus.NEEDS_ATTENTION,
                error="Publishing rights not confirmed. Copyright confirmation is required.",
                error_type="RIGHTS_UNCONFIRMED",
            )
            raise PermissionError(
                f"Cannot publish media '{media_item.id}': Rights and copyright permission "
                "have not been confirmed by the user. Run with rights confirmation."
            )

        # File validation
        if not media_item.file_path:
            raise FileNotFoundError(
                f"Media item '{media_item.id}' has no local file path recorded."
            )
        file_path = Path(media_item.file_path).resolve()
        if not file_path.is_file():
            raise FileNotFoundError(f"Media file on disk not found: {file_path}")
        file_size = file_path.stat().st_size
        if file_size == 0:
            raise ValueError(f"Media file on disk is empty (0 bytes): {file_path}")

        # Re-verify SHA-256 before uploading to prevent tampered or altered media
        if media_item.sha256:
            current_hash = compute_file_sha256(file_path)
            if current_hash.lower() != media_item.sha256.lower():
                self.db.update_job_status(
                    job.id,
                    PublishStatus.NEEDS_ATTENTION,
                    error="File hash mismatch before upload; file may have changed on disk.",
                    error_type="FILE_INTEGRITY_MISMATCH",
                )
                raise ValueError(
                    f"Integrity check failed for '{file_path}': "
                    "File content changed since archiving."
                )

        # Quota check
        allowed, used, limit = self.quota_manager.check_upload_quota(units_needed=1)
        if not allowed:
            self.db.update_job_status(
                job.id,
                PublishStatus.FAILED,
                error=f"Daily YouTube upload quota reached ({used}/{limit} units used).",
                error_type="QUOTA_EXCEEDED",
            )
            raise RuntimeError(
                f"Daily YouTube video upload limit reached ({used}/{limit} units used today). "
                "Pausing safely to preserve schedule."
            )

        service = youtube_service or self.auth_manager.build_service(job.channel_id)

        # Build YouTube API insert payload
        body: dict[str, Any] = {
            "snippet": {
                "title": metadata.title[:100],
                "description": metadata.description[:5000],
                "tags": metadata.tags if metadata.tags else [],
                "categoryId": str(metadata.category_id or "22"),
            },
            "status": {
                "privacyStatus": metadata.privacy_status,
                "selfDeclaredMadeForKids": bool(metadata.made_for_kids),
            },
        }

        # If scheduled publication
        if job.scheduled_publish_at:
            body["status"]["privacyStatus"] = "private"
            body["status"]["publishAt"] = job.scheduled_publish_at

        # Check existing resumable upload session for recovery
        existing_session = self.db.get_upload_session(job.id)
        if existing_session and existing_session.session_uri:
            recovered_id = self._try_reconcile_existing_session(
                existing_session.session_uri, file_size
            )
            if recovered_id:
                logger.info("Successfully reconciled existing upload session: %s", recovered_id)
                video_url = f"https://youtu.be/{recovered_id}"
                self.db.mark_job_published(job.id, recovered_id, video_url)
                self.db.delete_upload_session(job.id)
                self.quota_manager.record_upload(units=1)
                return recovered_id, video_url

        self.db.update_job_status(job.id, PublishStatus.UPLOADING)

        # Prepare MediaFileUpload
        media = MediaFileUpload(
            str(file_path),
            chunksize=CHUNK_SIZE,
            resumable=True,
            mimetype="video/*",
        )

        request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        retries = 0
        backoff = INITIAL_BACKOFF_SECONDS
        start_time = time.time()

        while response is None:
            try:
                # Save resumable URI as soon as it becomes available
                if (
                    hasattr(request, "resumable_uri")
                    and isinstance(request.resumable_uri, str)
                    and request.resumable_uri
                ):
                    session = UploadSession(
                        session_uri=request.resumable_uri,
                        job_id=job.id,
                        file_path=str(file_path),
                        file_size=file_size,
                        bytes_uploaded=int(getattr(request, "resumable_progress", 0)),
                        last_chunk_byte=int(getattr(request, "resumable_progress", 0)),
                    )
                    self.db.save_upload_session(session)

                status, response = request.next_chunk()
                retries = 0  # Reset retry counter on successful chunk
                backoff = INITIAL_BACKOFF_SECONDS

                if status:
                    bytes_sent = status.resumable_progress
                    total_bytes = status.total_size or file_size
                    elapsed = time.time() - start_time
                    speed = (bytes_sent / elapsed) if elapsed > 0 else 0.0

                    if (
                        hasattr(request, "resumable_uri")
                        and isinstance(request.resumable_uri, str)
                        and request.resumable_uri
                    ):
                        session = UploadSession(
                            session_uri=request.resumable_uri,
                            job_id=job.id,
                            file_path=str(file_path),
                            file_size=file_size,
                            bytes_uploaded=bytes_sent,
                            last_chunk_byte=bytes_sent,
                        )
                        self.db.save_upload_session(session)

                    if progress_callback:
                        progress_callback(bytes_sent, total_bytes, speed)

            except HttpError as http_err:
                status_code = http_err.resp.status
                if status_code in (500, 502, 503, 504):
                    # Transient server error: backoff with jitter
                    retries += 1
                    if retries > MAX_UPLOAD_RETRIES:
                        err_msg = (
                            f"Upload exhausted {MAX_UPLOAD_RETRIES} retries on HTTP "
                            f"{status_code}: {http_err}"
                        )
                        self.db.update_job_status(
                            job.id, PublishStatus.FAILED, error=err_msg, error_type="HTTP_ERROR"
                        )
                        raise
                    jitter = random.uniform(0.5, 1.5)
                    time.sleep(backoff * jitter)
                    backoff *= 2.0
                elif status_code == 403:
                    # Likely quota or permission error
                    err_msg = f"YouTube API 403 Forbidden: {http_err}"
                    self.db.update_job_status(
                        job.id, PublishStatus.FAILED, error=err_msg, error_type="QUOTA_OR_AUTH"
                    )
                    raise
                else:
                    err_msg = f"Fatal YouTube API Error ({status_code}): {http_err}"
                    self.db.update_job_status(
                        job.id, PublishStatus.FAILED, error=err_msg, error_type="FATAL_API"
                    )
                    raise

            except (OSError, requests.RequestException) as net_err:
                retries += 1
                if retries > MAX_UPLOAD_RETRIES:
                    err_msg = f"Network failure during chunk transfer: {net_err}"
                    self.db.update_job_status(
                        job.id,
                        PublishStatus.UPLOAD_INTERRUPTED,
                        error=err_msg,
                        error_type="NETWORK",
                    )
                    raise
                jitter = random.uniform(0.5, 1.5)
                time.sleep(backoff * jitter)
                backoff *= 2.0

        if not response or "id" not in response:
            raise RuntimeError(
                "YouTube API completed transfer but returned invalid response without video ID."
            )

        video_id = response["id"]
        video_url = f"https://youtu.be/{video_id}"

        # Post-upload verification
        self._verify_uploaded_video(service, video_id)

        # Finalize persistent state
        is_scheduled = bool(job.scheduled_publish_at)
        final_status = PublishStatus.SCHEDULED if is_scheduled else PublishStatus.PUBLISHED
        self.db.update_job_status(job.id, final_status)
        self.db.mark_job_published(job.id, video_id, video_url)
        self.db.delete_upload_session(job.id)

        # Record quota
        self.quota_manager.record_upload(units=1)

        return video_id, video_url

    def _try_reconcile_existing_session(self, session_uri: str, file_size: int) -> Optional[str]:
        """Query Google resumable upload endpoint to determine if upload previously completed."""
        try:
            headers = {"Content-Range": f"bytes */{file_size}"}
            resp = requests.put(session_uri, headers=headers, timeout=10.0)
            if resp.status_code in (200, 201):
                data = resp.json()
                if "id" in data:
                    return data["id"]
            return None
        except Exception as e:
            logger.debug("Failed session reconciliation: %s", e)
            return None

    def _verify_uploaded_video(self, service: Resource, video_id: str) -> dict[str, Any]:
        """Verify that YouTube Data API returns valid status for the newly uploaded video."""
        try:
            res = service.videos().list(part="id,status,snippet", id=video_id).execute()
            items = res.get("items", [])
            if items:
                return items[0]
        except Exception as e:
            logger.warning(
                "Could not verify video status immediately (%s), upload still registered", e
            )
        return {"id": video_id}
