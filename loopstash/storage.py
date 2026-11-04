"""Storage and directory management for LOOPSTASH media archives."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

from loopstash.metadata import (
    generate_manifest,
    serialize_reel_metadata,
    write_manifest,
    write_reel_metadata,
)
from loopstash.models import ReelItem
from loopstash.utils.hashing import compute_file_sha256
from loopstash.utils.paths import (
    ensure_directory,
    get_free_space,
    resolve_collision,
    sanitize_filename,
)

logger = logging.getLogger(__name__)


class StorageManager:
    """Manages cross-platform directory structures, atomic file moves, and disk checks."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir).resolve()
        self.temp_dir = self.base_dir / ".loopstash_tmp"
        ensure_directory(self.base_dir)
        ensure_directory(self.temp_dir)

    def get_profile_dir(self, username: str) -> Path:
        """Return the root archive folder for an Instagram profile."""
        clean_user = sanitize_filename(username)
        path = self.base_dir / "Instagram" / clean_user
        ensure_directory(path)
        return path

    def get_year_dir(self, username: str, year: str) -> Path:
        """Return year folder: LOOPSTASH/Instagram/<username>/<year>."""
        clean_year = sanitize_filename(year)
        path = self.get_profile_dir(username) / clean_year
        ensure_directory(path)
        return path

    def get_metadata_dir(self, username: str) -> Path:
        """Return metadata folder: LOOPSTASH/Instagram/<username>/metadata."""
        path = self.get_profile_dir(username) / "metadata"
        ensure_directory(path)
        return path

    def get_target_file_path(self, reel: ReelItem, extension: str = "mp4") -> Path:
        """Determine final target file path for a Reel."""
        year = reel.get_year()
        year_dir = self.get_year_dir(reel.username, year)
        clean_code = sanitize_filename(f"{reel.shortcode}.{extension.lstrip('.')}")
        return year_dir / clean_code

    def get_metadata_file_path(self, reel: ReelItem, filename_stem: Optional[str] = None) -> Path:
        """Determine path for a Reel's JSON metadata file."""
        meta_dir = self.get_metadata_dir(reel.username)
        stem = filename_stem or reel.shortcode
        clean_code = sanitize_filename(f"{stem}.json")
        return meta_dir / clean_code

    def get_manifest_path(self, username: str) -> Path:
        """Return path to profile manifest.json."""
        return self.get_profile_dir(username) / "manifest.json"

    def check_disk_space(self, required_mb: int = 500) -> tuple[bool, int]:
        """Check if storage destination has at least required_mb free space.

        Returns: (has_enough_space, free_mb)
        """
        free_bytes = get_free_space(self.base_dir)
        free_mb = free_bytes // (1024 * 1024)
        return (free_mb >= required_mb, free_mb)

    def prepare_temp_download_path(self, reel: ReelItem) -> Path:
        """Generate a temporary file path inside the staging temp directory."""
        ensure_directory(self.temp_dir)
        return self.temp_dir / f"{reel.shortcode}_{reel.username}.part"

    def finalize_reel(
        self,
        reel: ReelItem,
        downloaded_temp_file: Path,
    ) -> tuple[Path, int, str]:
        """Atomically finalize a downloaded Reel.

        1. Verify the temporary file exists and is non-empty.
        2. Compute SHA-256 hash.
        3. Move atomically to final target path, resolving collisions safely.
        4. Write metadata JSON.
        5. Return (final_path, file_size, sha256_hash).
        """
        if not downloaded_temp_file.is_file():
            raise FileNotFoundError(f"Downloaded temp file not found: {downloaded_temp_file}")

        file_size = downloaded_temp_file.stat().st_size
        if file_size == 0:
            downloaded_temp_file.unlink(missing_ok=True)
            raise ValueError(f"Downloaded file for Reel '{reel.shortcode}' is empty (0 bytes).")

        sha256 = compute_file_sha256(downloaded_temp_file)

        ext = downloaded_temp_file.suffix or ".mp4"
        target_path = self.get_target_file_path(reel, extension=ext)

        # Handle filename collisions safely
        if target_path.exists():
            # If target exists and has the identical hash, it's already properly saved
            existing_hash = compute_file_sha256(target_path)
            if existing_hash == sha256:
                downloaded_temp_file.unlink(missing_ok=True)
                final_path = target_path
            else:
                final_path = resolve_collision(target_path)
                shutil.move(str(downloaded_temp_file), str(final_path))
        else:
            final_path = target_path
            shutil.move(str(downloaded_temp_file), str(final_path))

        # Write metadata JSON
        meta_file = self.get_metadata_file_path(reel, filename_stem=final_path.stem)
        meta_dict = serialize_reel_metadata(
            reel=reel,
            sha256_hash=sha256,
            file_size=file_size,
            destination_file=str(final_path),
        )
        write_reel_metadata(meta_file, meta_dict)

        return final_path, file_size, sha256

    def update_profile_manifest(
        self, username: str, profile_url: str, archived_count: int, total_size_bytes: int
    ) -> None:
        """Update or create manifest.json for the archived profile."""
        manifest_data = generate_manifest(
            username=username,
            profile_url=profile_url,
            reels_count=archived_count,
            total_bytes=total_size_bytes,
        )
        write_manifest(self.get_manifest_path(username), manifest_data)

    def clean_temp_directory(self) -> None:
        """Purge lingering partial download files from .loopstash_tmp."""
        if self.temp_dir.exists():
            for item in self.temp_dir.iterdir():
                try:
                    if item.is_file() or item.is_symlink():
                        item.unlink(missing_ok=True)
                    elif item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                except Exception as e:
                    logger.debug("Failed cleaning temp file %s: %e", item, e)
