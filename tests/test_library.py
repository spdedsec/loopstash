"""Tests for media library management, metadata editing, search, verification, and import/export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopstash.database import Database
from loopstash.library.editor import MetadataEditor, validate_youtube_metadata
from loopstash.library.manager import LibraryManager
from loopstash.models import MediaItem
from loopstash.utils.hashing import compute_file_sha256


@pytest.fixture
def test_db_instance(tmp_path: Path) -> Database:
    db_file = tmp_path / "test_library.db"
    return Database(db_file)


def test_validate_youtube_metadata():
    """Test YouTube API metadata constraints."""
    # Valid metadata
    valid, errors = validate_youtube_metadata(
        "Clean Title", "A description", ["tag1", "tag2"], "public"
    )
    assert valid is True
    assert len(errors) == 0

    # Empty title
    valid, errors = validate_youtube_metadata("   ", "Description", [], "public")
    assert valid is False
    assert any("Title cannot be empty" in e for e in errors)

    # Title exceeds 100 chars
    long_title = "A" * 101
    valid, errors = validate_youtube_metadata(long_title, "Description", [], "public")
    assert valid is False
    assert any("exceeds maximum length of 100" in e for e in errors)

    # Description exceeds 5000 chars
    long_desc = "D" * 5001
    valid, errors = validate_youtube_metadata("Valid Title", long_desc, [], "public")
    assert valid is False
    assert any("exceeds maximum length of 5000" in e for e in errors)

    # Total tags length exceeds 500 chars
    long_tags = ["tag" + str(i) * 50 for i in range(15)]
    valid, errors = validate_youtube_metadata("Valid Title", "Desc", long_tags, "public")
    assert valid is False
    assert any("Combined tags length exceeds 500" in e for e in errors)

    # Invalid privacy
    valid, errors = validate_youtube_metadata("Valid Title", "Desc", ["tag"], "secret_unlisted")
    assert valid is False
    assert any("Invalid privacy status" in e for e in errors)


def test_metadata_editor_and_unicode_preservation(test_db_instance: Database):
    """Test editing publishing metadata and preserving Unicode creator captions."""
    db = test_db_instance
    editor = MetadataEditor(db)

    # Create media item with Unicode text (Japanese, Hindi, Emojis)
    unicode_title = "こんにちは 世界 🚀 नमस्ते"
    unicode_desc = "Testing Unicode preservation: #tech #लूपस्टैश 🎥"
    item = MediaItem(
        id="reel_uni_1",
        source_platform="instagram",
        source_id="uni_1",
        username="global_creator",
        title=unicode_title,
        caption=unicode_desc,
    )
    db.upsert_media_item(item)

    # Apply edit
    updated = editor.edit(
        media_id="reel_uni_1",
        title="Edited: こんにちは 🌟",
        description="Edited desc with Hindi: नमस्कार",
        tags=["technology", "ग्लोबल"],
        privacy_status="public",
        rights_confirmed=True,
    )

    assert updated.title == "Edited: こんにちは 🌟"
    assert updated.rights_confirmed is True
    assert "ग्लोबल" in updated.tags

    # Retrieve from DB and verify persistence
    stored = db.get_publishing_metadata("reel_uni_1")
    assert stored is not None
    assert stored.title == "Edited: こんにちは 🌟"
    assert stored.description == "Edited desc with Hindi: नमस्कार"
    assert stored.rights_confirmed is True


def test_library_manager_search_and_filtering(test_db_instance: Database):
    """Test searching across titles, captions, creators, and hashtags."""
    db = test_db_instance
    mgr = LibraryManager(db)

    item1 = MediaItem(
        id="item_001",
        username="alice",
        title="Python Performance Guide",
        caption="Deep dive into Python 3.14 features #python #coding",
    )
    item2 = MediaItem(
        id="item_002",
        username="bob",
        title="Linux Terminal Mastery",
        caption="How to use Neovim and bash #linux #terminal",
    )
    item3 = MediaItem(
        id="item_003",
        username="alice",
        title="Database Indexing",
        caption="SQLite WAL mode and indexing #database #sqlite",
    )

    db.upsert_media_item(item1)
    db.upsert_media_item(item2)
    db.upsert_media_item(item3)

    # Filter by username
    alice_items = mgr.list_items(username="alice")
    assert len(alice_items) == 2
    assert {i.id for i in alice_items} == {"item_001", "item_003"}

    # Search query
    res = mgr.search("terminal")
    assert len(res) == 1
    assert res[0].id == "item_002"

    res_py = mgr.search("python")
    assert len(res_py) == 1
    assert res_py[0].id == "item_001"


def test_library_verification_detects_missing_and_corrupt(
    test_db_instance: Database, tmp_path: Path
):
    """Test verify_library detecting healthy, missing, and corrupted files."""
    db = test_db_instance
    mgr = LibraryManager(db)

    # 1. Healthy file
    good_file = tmp_path / "good.mp4"
    good_file.write_bytes(b"VALID_VIDEO_CONTENT_DATA_12345")
    good_sha = compute_file_sha256(good_file)
    item_good = MediaItem(
        id="good_01",
        file_path=str(good_file),
        file_size=good_file.stat().st_size,
        sha256=good_sha,
    )
    db.upsert_media_item(item_good)

    # 2. Missing file
    missing_path = tmp_path / "non_existent.mp4"
    item_missing = MediaItem(
        id="missing_01",
        file_path=str(missing_path),
        file_size=1000,
        sha256="abc12345",
    )
    db.upsert_media_item(item_missing)

    # 3. Corrupted file (content modified after registering hash)
    corrupt_file = tmp_path / "corrupt.mp4"
    corrupt_file.write_bytes(b"INITIAL_ORIGINAL_BYTES")
    orig_sha = compute_file_sha256(corrupt_file)
    item_corrupt = MediaItem(
        id="corrupt_01",
        file_path=str(corrupt_file),
        file_size=corrupt_file.stat().st_size,
        sha256=orig_sha,
    )
    db.upsert_media_item(item_corrupt)
    # Modify content to induce hash mismatch
    corrupt_file.write_bytes(b"TAMPERED_MODIFIED_BYTES_67890")

    report = mgr.verify_library()
    assert report["total_checked"] == 3
    assert report["verified"] == 1
    assert report["missing"] == 1
    assert report["corrupted"] == 1


def test_library_export_json_and_csv(test_db_instance: Database, tmp_path: Path):
    """Test exporting catalog to JSON and CSV formats."""
    db = test_db_instance
    mgr = LibraryManager(db)

    item = MediaItem(
        id="export_01",
        username="exporter",
        title="Export Test Title",
        caption="Sample caption #export",
        file_path="/tmp/fake.mp4",
        file_size=2048,
        sha256="deadbeef",
    )
    db.upsert_media_item(item)

    # JSON export
    json_out = tmp_path / "export.json"
    mgr.export_metadata(json_out, export_format="json")
    assert json_out.is_file()
    data = json.loads(json_out.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["id"] == "export_01"
    assert data[0]["username"] == "exporter"

    # CSV export
    csv_out = tmp_path / "export.csv"
    mgr.export_metadata(csv_out, export_format="csv")
    assert csv_out.is_file()
    content = csv_out.read_text(encoding="utf-8")
    assert "export_01" in content
    assert "Export Test Title" in content


def test_library_import_directory_with_sidecars(test_db_instance: Database, tmp_path: Path):
    """Test scanning and importing a folder with videos and JSON sidecars."""
    db = test_db_instance
    mgr = LibraryManager(db)

    import_dir = tmp_path / "import_source"
    import_dir.mkdir()

    # Create dummy video 1 with sidecar
    vid1 = import_dir / "vid1.mp4"
    vid1.write_bytes(b"VIDEO_ONE_CONTENT")
    sidecar1 = import_dir / "vid1.json"
    sidecar1.write_text(
        json.dumps(
            {
                "title": "Imported Video One",
                "caption": "Imported caption #first",
                "tags": ["first", "test"],
            }
        ),
        encoding="utf-8",
    )

    # Create dummy video 2 without sidecar
    vid2 = import_dir / "vid2.mp4"
    vid2.write_bytes(b"VIDEO_TWO_CONTENT")

    results = mgr.import_directory(import_dir, username="test_importer")
    assert results["imported"] == 2
    assert results["skipped_duplicates"] == 0

    # Verify video 1 in DB
    items = mgr.list_items()
    assert len(items) == 2
    vid1_item = next(
        i for i in items if "vid1" in (i.title or "") or "Imported Video One" in (i.title or "")
    )
    assert vid1_item.username == "test_importer"

    # Re-import same folder: duplicates should be safely skipped!
    results2 = mgr.import_directory(import_dir, username="test_importer")
    assert results2["imported"] == 0
    assert results2["skipped_duplicates"] == 2


def test_library_manager_status_filters_and_exclusion(test_db_instance: Database):
    """Test filtering by download status, publish status, and excluding items."""
    db = test_db_instance
    mgr = LibraryManager(db)

    from loopstash.models import DownloadStatus, JobRecord, PublishingJob, PublishStatus

    # Insert items
    item1 = MediaItem(id="flt_01", username="creator1", title="Video 1")
    item2 = MediaItem(id="flt_02", username="creator2", title="Video 2")
    db.upsert_media_item(item1)
    db.upsert_media_item(item2)

    # Reels tracking download status
    db.upsert_profile("creator1", "https://instagram.com/creator1/")
    db.upsert_profile("creator2", "https://instagram.com/creator2/")
    db.upsert_reel(
        JobRecord(
            shortcode="flt_01",
            username="creator1",
            profile_url="https://instagram.com/creator1/",
            reel_url="https://instagram.com/reel/flt_01/",
            status=DownloadStatus.COMPLETED,
        )
    )
    db.upsert_reel(
        JobRecord(
            shortcode="flt_02",
            username="creator2",
            profile_url="https://instagram.com/creator2/",
            reel_url="https://instagram.com/reel/flt_02/",
            status=DownloadStatus.FAILED,
        )
    )

    # Publishing account and job tracking publish status
    from loopstash.models import PublishingAccount

    db.upsert_publishing_account(
        PublishingAccount(
            id="UC_CH_1",
            channel_id="UC_CH_1",
            channel_title="Filter Test Channel",
            token_storage_key="file:UC_CH_1",
        )
    )
    db.upsert_publishing_job(
        PublishingJob(
            id="job_flt_01",
            media_id="flt_01",
            channel_id="UC_CH_1",
            status=PublishStatus.PUBLISHED,
        )
    )

    # 1. Filter by download status
    completed = mgr.list_items(download_status="COMPLETED")
    assert len(completed) == 1
    assert completed[0].id == "flt_01"

    failed = mgr.list_items(download_status="FAILED")
    assert len(failed) == 1
    assert failed[0].id == "flt_02"

    # 2. Filter by publish status
    published = mgr.list_items(publish_status="PUBLISHED")
    assert len(published) == 1
    assert published[0].id == "flt_01"

    # 3. Test exclude_item
    assert mgr.exclude_item("flt_01", is_excluded=True) is True
    # Default list excludes excluded items
    active_items = mgr.list_items(excluded=False)
    assert len(active_items) == 1
    assert active_items[0].id == "flt_02"

    # Undo exclude
    assert mgr.exclude_item("flt_01", is_excluded=False) is True
    assert len(mgr.list_items(excluded=False)) == 2
