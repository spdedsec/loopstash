"""Storage module for LOOPSTASH."""

from __future__ import annotations

from loopstash.storage.base import BaseStorageAdapter
from loopstash.storage.local import LocalStorageAdapter
from loopstash.storage.manager import StorageManager

__all__ = ["BaseStorageAdapter", "LocalStorageAdapter", "StorageManager"]
