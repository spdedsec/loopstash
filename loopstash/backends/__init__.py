"""Backend adapters for media extraction."""

from loopstash.backends.base import (
    AuthenticationRequiredError,
    BackendError,
    BackendNotFoundError,
    BaseBackend,
    DownloadError,
    NetworkError,
    ProfileNotFoundError,
    RateLimitError,
)
from loopstash.backends.gallery_dl import GalleryDlBackend, find_gallery_dl_binary

__all__ = [
    "AuthenticationRequiredError",
    "BackendError",
    "BackendNotFoundError",
    "BaseBackend",
    "DownloadError",
    "NetworkError",
    "ProfileNotFoundError",
    "RateLimitError",
    "GalleryDlBackend",
    "find_gallery_dl_binary",
]
