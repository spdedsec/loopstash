"""Tests for storage management, directory structure, atomic finalization, and path sanitization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopstash.models import ReelItem
from loopstash.storage import StorageManager
from loopstash.utils.paths import resolve_collision, sanitize_filename


def test_storage_directory_hierarchy(tmp_dir: Path, sample_reel: ReelItem):
    """Test standard directory structure creation."""
    storage = StorageManager(tmp_dir / "vault")
    profile_dir = storage.get_profile_dir(sample_reel.username)
    year_dir = storage.get_year_dir(sample_reel.username, "2026")
    meta_dir = storage.get_metadata_dir(sample_reel.username)

    assert profile_dir.is_dir()
    assert year_dir.is_dir()
    assert meta_dir.is_dir()
    assert year_dir.parent == profile_dir
    assert meta_dir.parent == profile_dir


def test_sanitize_filename_cross_platform():
    """Test filename sanitization for Windows, Linux, and macOS."""
    # Illegal chars stripped / replaced
    assert sanitize_filename("invalid:name*with?chars") == "invalid_name_with_chars"
    assert sanitize_filename('test"quoted"|pipe<greater>less') == "test_quoted__pipe_greater_less"

    # Leading / trailing dots and spaces
    assert sanitize_filename("  .hidden_file.  ") == "hidden_file"

    # Windows reserved names
    assert sanitize_filename("con.mp4") == "con_file.mp4"
    assert sanitize_filename("AUX.json") == "AUX_file.json"
    assert sanitize_filename("NUL") == "NUL_file"
    assert sanitize_filename("com1.txt") == "com1_file.txt"

    # Unicode filenames
    unicode_name = "日本語_reel_🎥_archive.mp4"
    sanitized = sanitize_filename(unicode_name)
    assert "日本語" in sanitized
    assert "🎥" in sanitized

    # Max length truncation
    long_name = "a" * 300 + ".mp4"
    truncated = sanitize_filename(long_name, max_length=50)
    assert len(truncated) <= 50
    assert truncated.endswith(".mp4")

    # Empty name fallback
    assert sanitize_filename("") == "unnamed"
    assert sanitize_filename("   ...   ") == "unnamed"


def test_paths_with_spaces_and_unicode(tmp_dir: Path):
    """Test storage handling with paths containing spaces and Unicode characters."""
    special_dir = tmp_dir / "External HDD 2026" / "My Archives 📁"
    storage = StorageManager(special_dir)

    reel = ReelItem(
        shortcode="Code 123",
        username="user with spaces 🚀",
        reel_url="https://instagram.com/reel/Code123/",
    )

    p_dir = storage.get_profile_dir(reel.username)
    assert p_dir.is_dir()
    assert "user with spaces 🚀" in p_dir.name

    target = storage.get_target_file_path(reel)
    assert "Code 123.mp4" in target.name


def test_resolve_collision(tmp_dir: Path):
    """Test resolving filename collisions without overwriting existing media."""
    f1 = tmp_dir / "video.mp4"
    f1.write_bytes(b"DATA1")

    # No collision on non-existent file
    non_existent = tmp_dir / "new_video.mp4"
    assert resolve_collision(non_existent) == non_existent

    # Collision when file exists
    c1 = resolve_collision(f1)
    assert c1 == tmp_dir / "video_1.mp4"

    # Create video_1.mp4 and verify video_2.mp4
    c1.write_bytes(b"DATA2")
    c2 = resolve_collision(f1)
    assert c2 == tmp_dir / "video_2.mp4"


def test_finalize_reel_atomic(tmp_dir: Path, sample_reel: ReelItem):
    """Test atomic finalization, hashing, metadata writing, and collision handling."""
    storage = StorageManager(tmp_dir / "storage")

    # Prepare temp file
    temp_file = storage.prepare_temp_download_path(sample_reel)
    dummy_data = b"REAL_VIDEO_MEDIA_CONTENT_ABCDEF"
    temp_file.write_bytes(dummy_data)

    final_path, file_size, sha256 = storage.finalize_reel(sample_reel, temp_file)

    assert final_path.is_file()
    assert not temp_file.exists()  # Moved atomically
    assert file_size == len(dummy_data)
    assert len(sha256) == 64

    # Verify metadata JSON was created
    meta_file = storage.get_metadata_file_path(sample_reel)
    assert meta_file.is_file()
    with meta_file.open("r", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["shortcode"] == sample_reel.shortcode
    assert meta["sha256"] == sha256
    assert meta["file_size_bytes"] == file_size

    # Finalize another temp file with different data for same shortcode (collision)
    temp_file2 = storage.prepare_temp_download_path(sample_reel)
    temp_file2.write_bytes(b"DIFFERENT_CONTENT_FOR_COLLISION")

    final_path2, size2, hash2 = storage.finalize_reel(sample_reel, temp_file2)
    assert final_path2 != final_path
    assert "_1" in final_path2.stem
    assert final_path.is_file()
    assert final_path2.is_file()

    # Verify both metadata files exist independently
    meta_file2 = storage.get_metadata_file_path(sample_reel, filename_stem=final_path2.stem)
    assert meta_file.is_file()
    assert meta_file2.is_file()
    assert meta_file2 != meta_file


def test_finalize_reel_empty_or_missing_file_error(tmp_dir: Path, sample_reel: ReelItem):
    """Test that empty or non-existent files are rejected."""
    storage = StorageManager(tmp_dir / "storage")

    # Missing file
    missing_file = tmp_dir / "nonexistent.part"
    with pytest.raises(FileNotFoundError):
        storage.finalize_reel(sample_reel, missing_file)

    # Empty 0-byte file
    empty_file = tmp_dir / "empty.part"
    empty_file.touch()
    with pytest.raises(ValueError, match="is empty"):
        storage.finalize_reel(sample_reel, empty_file)
    assert not empty_file.exists()  # Cleaned up


def test_manifest_and_temp_cleanup(tmp_dir: Path):
    """Test profile manifest generation and temp directory cleaning."""
    storage = StorageManager(tmp_dir / "storage")

    # Temp cleanup
    t1 = storage.temp_dir / "file1.part"
    t1.write_bytes(b"leftover")
    storage.clean_temp_directory()
    assert not t1.exists()

    # Manifest update
    storage.update_profile_manifest(
        username="influencer",
        profile_url="https://instagram.com/influencer/",
        archived_count=42,
        total_size_bytes=104857600,
    )

    manifest_path = storage.get_manifest_path("influencer")
    assert manifest_path.is_file()
    with manifest_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest["username"] == "influencer"
    assert manifest["total_archived_reels"] == 42
    assert manifest["total_size_bytes"] == 104857600
    assert "last_updated" in manifest
