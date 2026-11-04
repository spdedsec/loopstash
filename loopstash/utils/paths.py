"""Cross-platform path and filesystem utilities."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import platformdirs

_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}

_INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def get_default_config_dir() -> Path:
    """Return OS-specific configuration directory for LOOPSTASH."""
    path = Path(platformdirs.user_config_dir("loopstash"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_default_data_dir() -> Path:
    """Return OS-specific data directory for LOOPSTASH SQLite database and state."""
    path = Path(platformdirs.user_data_dir("loopstash"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_default_download_dir() -> Path:
    """Return default download destination directory."""
    try:
        base = Path(platformdirs.user_videos_dir())
    except Exception:
        base = Path.home() / "Videos"

    if not base.exists() and not base.parent.exists():
        base = Path.home() / "LOOPSTASH"
    else:
        base = base / "LOOPSTASH"

    return base


def sanitize_filename(name: str, max_length: int = 200) -> str:
    """Sanitize a filename for cross-platform compatibility (Linux, Windows, macOS)."""
    if not name:
        return "unnamed"

    # Strip null bytes and illegal characters
    cleaned = _INVALID_CHARS_RE.sub("_", name)
    # Strip leading/trailing spaces and dots (problematic on Windows)
    cleaned = cleaned.strip(". ")

    if not cleaned:
        cleaned = "unnamed"

    stem, ext = os.path.splitext(cleaned)
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        stem = f"{stem}_file"

    # Truncate if exceeds max_length
    if len(stem) + len(ext) > max_length:
        stem = stem[: max(1, max_length - len(ext))]

    return f"{stem}{ext}"


def ensure_directory(path: Path) -> Path:
    """Create directory and its parents if they do not exist."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_free_space(path: Path) -> int:
    """Return available free storage space in bytes at the specified path."""
    current = path.resolve()
    while not current.exists() and current != current.parent:
        current = current.parent
    try:
        return shutil.disk_usage(current).free
    except Exception:
        return 0


def resolve_collision(target_path: Path) -> Path:
    """If target_path exists, generate a non-colliding alternative (e.g., file_1.mp4)."""
    if not target_path.exists():
        return target_path

    parent = target_path.parent
    stem = target_path.stem
    suffix = target_path.suffix

    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
