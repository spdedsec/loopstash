"""Metadata serialization and manifest management for LOOPSTASH."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loopstash.models import ReelItem


def serialize_reel_metadata(
    reel: ReelItem,
    sha256_hash: Optional[str] = None,
    file_size: Optional[int] = None,
    destination_file: Optional[str] = None,
) -> dict[str, Any]:
    """Serialize a ReelItem into a standardized JSON-compatible metadata dictionary."""
    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        "shortcode": reel.shortcode,
        "media_id": reel.media_id,
        "username": reel.username,
        "profile_url": reel.profile_url,
        "reel_url": reel.reel_url,
        "title": reel.title,
        "caption": reel.caption,
        "upload_timestamp": reel.upload_timestamp,
        "archived_timestamp": now_iso,
        "file_size_bytes": file_size,
        "sha256": sha256_hash,
        "destination_file": destination_file,
        "duration_seconds": reel.duration,
        "dimensions": {
            "width": reel.width,
            "height": reel.height,
        },
        "raw_extractor_metadata": reel.extra_metadata,
    }


def write_reel_metadata(path: Path, data: dict[str, Any]) -> None:
    """Safely write reel metadata dictionary as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp_path.replace(path)


def read_reel_metadata(path: Path) -> Optional[dict[str, Any]]:
    """Read reel metadata from JSON file."""
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def generate_manifest(
    username: str,
    profile_url: str,
    reels_count: int,
    total_bytes: int,
    last_sync: Optional[str] = None,
) -> dict[str, Any]:
    """Generate manifest dictionary for an archived profile."""
    now_iso = last_sync or datetime.now(timezone.utc).isoformat()
    return {
        "format": "loopstash-manifest-v1",
        "username": username,
        "profile_url": profile_url,
        "last_updated": now_iso,
        "total_archived_reels": reels_count,
        "total_size_bytes": total_bytes,
        "generator": "LOOPSTASH",
    }


def write_manifest(manifest_path: Path, manifest_data: dict[str, Any]) -> None:
    """Safely write manifest dictionary to manifest.json."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = manifest_path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)
    tmp_path.replace(manifest_path)
