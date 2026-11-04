"""Data models for LOOPSTASH."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class DownloadStatus(str, Enum):
    """Lifecycle states for a Reel download job."""

    PENDING = "PENDING"
    DISCOVERING = "DISCOVERING"
    DOWNLOADING = "DOWNLOADING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
    UNAVAILABLE = "UNAVAILABLE"


class PublishStatus(str, Enum):
    """Lifecycle states for a media publishing / YouTube upload job."""

    READY = "READY"
    QUEUED = "QUEUED"
    UPLOADING = "UPLOADING"
    UPLOAD_INTERRUPTED = "UPLOAD_INTERRUPTED"
    UPLOADED_PRIVATE = "UPLOADED_PRIVATE"
    SCHEDULED = "SCHEDULED"
    PROCESSING = "PROCESSING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"


@dataclass
class ReelItem:
    """Represents an extracted Instagram Reel prior to or during download."""

    shortcode: str
    username: str
    reel_url: str
    media_id: Optional[str] = None
    profile_url: Optional[str] = None
    title: Optional[str] = None
    caption: Optional[str] = None
    upload_timestamp: Optional[str] = None
    video_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    extra_metadata: dict[str, Any] = field(default_factory=dict)

    def get_year(self) -> str:
        """Derive the 4-digit upload year for directory structuring."""
        if self.upload_timestamp:
            try:
                val = str(self.upload_timestamp).strip()
                try:
                    ts = float(val)
                    if ts > 100000000:
                        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                        return str(dt.year)
                except ValueError:
                    pass
                if len(val) >= 4 and val[:4].isdigit():
                    return val[:4]
            except Exception:
                pass
        return str(datetime.now(timezone.utc).year)


@dataclass
class JobRecord:
    """Represents a persistent database tracking record for a Reel."""

    shortcode: str
    username: str
    profile_url: str
    reel_url: str
    status: DownloadStatus = DownloadStatus.PENDING
    media_id: Optional[str] = None
    destination_path: Optional[str] = None
    file_size: Optional[int] = None
    sha256_hash: Optional[str] = None
    upload_timestamp: Optional[str] = None
    download_timestamp: Optional[str] = None
    retry_count: int = 0
    last_error: Optional[str] = None
    backend: str = "gallery-dl"
    last_sync: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class ProfileRecord:
    """Represents an archived profile's aggregate statistics."""

    username: str
    profile_url: str
    first_seen: str
    last_sync: Optional[str] = None
    total_discovered: int = 0
    completed_count: int = 0
    failed_count: int = 0
    duplicate_count: int = 0


@dataclass
class DownloadProgress:
    """Real-time progress reporting during Reel downloads."""

    shortcode: str
    status: DownloadStatus
    total_reels: int = 0
    completed_reels: int = 0
    duplicate_reels: int = 0
    failed_reels: int = 0
    remaining_reels: int = 0
    current_file: Optional[str] = None
    speed_bps: Optional[float] = None
    message: Optional[str] = None


@dataclass
class DiagnosticCheck:
    """Outcome of an environment diagnostic check."""

    name: str
    status: str  # "PASS", "FAIL", "WARN"
    details: str
    remediation: Optional[str] = None


@dataclass
class MediaItem:
    """Represents a cataloged media asset in the media library."""

    id: str
    source_platform: str = "instagram"
    source_id: str = ""
    username: Optional[str] = None
    title: Optional[str] = None
    caption: Optional[str] = None
    file_path: Optional[str] = None
    file_size: Optional[int] = None
    sha256: Optional[str] = None
    duration: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    upload_timestamp: Optional[str] = None
    download_timestamp: Optional[str] = None
    is_excluded: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class MediaMetadata:
    """Preserved raw source metadata and extracted media properties."""

    media_id: str
    raw_json: str = "{}"
    hashtags: list[str] = field(default_factory=list)
    audio_info: Optional[str] = None
    thumbnail_url: Optional[str] = None
    thumbnail_path: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PublishingMetadata:
    """User-editable publishing metadata mapped to platform requirements."""

    media_id: str
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    category_id: str = "22"  # YouTube default: People & Blogs
    privacy_status: str = "public"
    made_for_kids: bool = False
    thumbnail_path: Optional[str] = None
    language: Optional[str] = None
    rights_confirmed: bool = False
    rights_notes: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class PublishingAccount:
    """Authenticated publishing account / destination channel."""

    id: str
    platform: str = "youtube"
    account_name: str = ""
    channel_id: str = ""
    channel_title: str = ""
    token_storage_key: str = ""
    is_active: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class PublishingJob:
    """Persistent tracking of an upload / publication execution."""

    id: str
    media_id: str
    channel_id: str
    platform: str = "youtube"
    status: PublishStatus = PublishStatus.READY
    scheduled_publish_at: Optional[str] = None
    display_timezone: Optional[str] = None
    upload_session_uri: Optional[str] = None
    bytes_uploaded: int = 0
    youtube_video_id: Optional[str] = None
    youtube_video_url: Optional[str] = None
    publication_date: Optional[str] = None
    retry_count: int = 0
    last_error: Optional[str] = None
    error_type: Optional[str] = None
    rights_confirmed: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None


@dataclass
class ScheduleEntry:
    """Scheduled publication calendar slot."""

    id: str
    job_id: str
    media_id: str
    channel_id: str
    scheduled_utc: str
    local_display_time: str
    timezone_name: str
    interval_spec: str = "daily"
    status: PublishStatus = PublishStatus.SCHEDULED
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class UploadSession:
    """Resumable upload session state for interrupted transfer recovery."""

    session_uri: str
    job_id: str
    file_path: str
    file_size: int
    bytes_uploaded: int
    last_chunk_byte: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class QuotaRecord:
    """Daily YouTube API quota usage tracking."""

    date_utc: str
    upload_units_used: int = 0
    api_requests_count: int = 0
    daily_limit: int = 100
    last_request_time: Optional[str] = None
