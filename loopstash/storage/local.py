"""Local filesystem storage adapter implementation."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Optional

from loopstash.storage.base import BaseStorageAdapter
from loopstash.utils.paths import get_free_space


class LocalStorageAdapter(BaseStorageAdapter):
    """Storage adapter for local filesystems, external drives, and mounted shares."""

    def __init__(self, root_dir: Optional[str | Path] = None):
        self.root_dir = Path(root_dir).resolve() if root_dir else None

    def _resolve(self, path: str | Path) -> Path:
        p = Path(path)
        if p.is_absolute() or self.root_dir is None:
            return p.resolve()
        return (self.root_dir / p).resolve()

    def exists(self, path: str | Path) -> bool:
        return self._resolve(path).exists()

    def get_size(self, path: str | Path) -> int:
        p = self._resolve(path)
        if not p.is_file():
            raise FileNotFoundError(f"File not found: {p}")
        return p.stat().st_size

    def open_read(self, path: str | Path) -> BinaryIO:
        p = self._resolve(path)
        if not p.is_file():
            raise FileNotFoundError(f"File not found: {p}")
        return p.open("rb")

    def get_free_space(self, target_dir: Optional[str | Path] = None) -> int:
        dir_to_check = Path(target_dir) if target_dir else (self.root_dir or Path.cwd())
        return get_free_space(dir_to_check)

    def get_local_path(self, path: str | Path) -> Path:
        return self._resolve(path)
