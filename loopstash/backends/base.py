"""Base abstraction and exception hierarchy for LOOPSTASH media extraction backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from loopstash.models import ReelItem


class BackendError(Exception):
    """Base exception for all extraction backend errors."""

    def __init__(self, message: str, retriable: bool = False):
        super().__init__(message)
        self.message = message
        self.retriable = retriable


class BackendNotFoundError(BackendError):
    """Raised when the backend binary (e.g. gallery-dl) is missing or not installed."""

    def __init__(self, message: str = "Extraction backend executable not found."):
        super().__init__(message, retriable=False)


class ProfileNotFoundError(BackendError):
    """Raised when the requested Instagram profile does not exist (HTTP 404)."""

    def __init__(self, message: str = "Instagram profile not found."):
        super().__init__(message, retriable=False)


class AuthenticationRequiredError(BackendError):
    """Raised when Instagram demands login, CAPTCHA, or profile is private."""

    def __init__(
        self,
        message: str = (
            "Instagram authentication required (profile may be private or login challenge issued)."
        ),
    ):
        super().__init__(message, retriable=False)


class RateLimitError(BackendError):
    """Raised when Instagram responds with HTTP 429 Too Many Requests."""

    def __init__(
        self,
        message: str = "Instagram rate limit reached (HTTP 429). Please wait before trying again.",
        retry_after: Optional[int] = None,
    ):
        super().__init__(message, retriable=True)
        self.retry_after = retry_after


class NetworkError(BackendError):
    """Raised on connection timeout, DNS failure, or network drop."""

    def __init__(self, message: str = "Network connection failed."):
        super().__init__(message, retriable=True)


class DownloadError(BackendError):
    """Raised when media download fails during stream retrieval."""

    def __init__(self, message: str = "Failed to download media item.", retriable: bool = True):
        super().__init__(message, retriable=retriable)


class BaseBackend(ABC):
    """Abstract interface for media extraction and download backends."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend identifier name."""
        ...

    @abstractmethod
    def check_availability(self) -> tuple[bool, str]:
        """Check if backend is installed and return (is_available, version_or_error)."""
        ...

    @abstractmethod
    def extract_reels(
        self,
        username: str,
        profile_url: str,
        browser: Optional[str] = None,
        max_items: Optional[int] = None,
    ) -> list[ReelItem]:
        """Discover and list all Reels for an Instagram profile.

        Must extract ONLY Reels, filtering out photos, carousels, and regular posts.
        """
        ...

    @abstractmethod
    def download_reel(
        self,
        reel: ReelItem,
        target_file: Path,
        temp_file: Path,
        browser: Optional[str] = None,
    ) -> Path:
        """Download single Reel video to temp_file in original highest available quality."""
        ...
