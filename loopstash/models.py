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
                # ISO format or YYYY-MM-DD
                return self.upload_timestamp[:4]
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
