"""Abstract base storage adapter interface for LOOPSTASH media items."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Optional


class BaseStorageAdapter(ABC):
    """Abstract interface for local and remote storage providers."""

    @abstractmethod
    def exists(self, path: str | Path) -> bool:
        """Check if target path exists."""
        pass

    @abstractmethod
    def get_size(self, path: str | Path) -> int:
        """Get file size in bytes."""
        pass

    @abstractmethod
    def open_read(self, path: str | Path) -> BinaryIO:
        """Open binary stream for reading."""
        pass

    @abstractmethod
    def get_free_space(self, target_dir: Optional[str | Path] = None) -> int:
        """Return free space in bytes."""
        pass

    @abstractmethod
    def get_local_path(self, path: str | Path) -> Path:
        """Return resolved local filesystem path."""
        pass
