"""Media library management, search, verification, and file import/export."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loopstash.database import Database
from loopstash.models import DownloadStatus, JobRecord, MediaItem, PublishingMetadata
from loopstash.utils.hashing import compute_file_sha256


class LibraryManager:
    """Core manager for querying, cataloging, verifying, and exporting library assets."""

    def __init__(self, db: Database):
        self.db = db

    def list_items(
        self,
        username: Optional[str] = None,
        excluded: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MediaItem]:
        """Retrieve media items with optional filters."""
        return self.db.get_all_media_items(
            username=username,
            excluded=excluded,
            limit=limit,
            offset=offset,
        )

    def search(self, query: str, limit: int = 50) -> list[MediaItem]:
        """Search items across titles, captions, creators, and hashtags."""
        clean_q = query.strip()
        if not clean_q:
            return self.list_items(limit=limit)
        return self.db.search_media_items(clean_q, limit=limit)

    def get_item_details(self, media_id: str) -> Optional[dict[str, Any]]:
        """Return composite record for a media item including file verification and jobs."""
        item = self.db.get_media_item(media_id)
        if not item:
            return None

        pub_meta = self.db.get_publishing_metadata(media_id)
        jobs = self.db.get_publishing_jobs_by_media(media_id)
        schedule = self.db.get_schedule_by_media(media_id)

        file_exists = False
        disk_size = None
        hash_matches = None

        if item.file_path:
            p = Path(item.file_path)
            file_exists = p.is_file()
            if file_exists:
                disk_size = p.stat().st_size
                if item.sha256:
                    actual_hash = compute_file_sha256(p)
                    hash_matches = actual_hash.lower() == item.sha256.lower()

        return {
            "media_item": item,
            "publishing_metadata": pub_meta,
            "publishing_jobs": jobs,
            "schedule": schedule,
            "file_exists": file_exists,
            "disk_size": disk_size,
            "hash_matches": hash_matches,
        }

    def verify_library(self, username: Optional[str] = None) -> dict[str, Any]:
        """Verify on-disk file integrity and SHA-256 digests for library assets."""
        items = self.db.get_all_media_items(username=username, limit=10000)
        results: dict[str, Any] = {
            "total_checked": len(items),
            "verified": 0,
            "missing": 0,
            "corrupted": 0,
            "details": [],  # (media_id, status, reason)
        }

        for it in items:
            if not it.file_path:
                results["missing"] += 1
                results["details"].append((it.id, "MISSING", "No file path recorded"))
                continue

            p = Path(it.file_path)
            if not p.is_file():
                results["missing"] += 1
                results["details"].append((it.id, "MISSING", f"File not found: {p}"))
                continue

            # Verify size
            actual_size = p.stat().st_size
            if actual_size == 0:
                results["corrupted"] += 1
                results["details"].append((it.id, "CORRUPTED", "File size is 0 bytes"))
                continue

            # Verify SHA-256
            if it.sha256:
                actual_hash = compute_file_sha256(p)
                if actual_hash.lower() != it.sha256.lower():
                    results["corrupted"] += 1
                    reason_msg = (
                        f"SHA-256 mismatch: recorded {it.sha256[:10]}, actual {actual_hash[:10]}"
                    )
                    results["details"].append((it.id, "CORRUPTED", reason_msg))
                    continue

            results["verified"] += 1
            results["details"].append((it.id, "OK", "Intact and verified"))

        return results

    def export_metadata(
        self,
        output_path: Path,
        export_format: str = "json",
        username: Optional[str] = None,
    ) -> Path:
        """Export media library catalog and publishing metadata to JSON or CSV."""
        items = self.db.get_all_media_items(username=username, limit=100000)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        records = []
        for it in items:
            pub = self.db.get_publishing_metadata(it.id)
            records.append(
                {
                    "id": it.id,
                    "source_platform": it.source_platform,
                    "source_id": it.source_id,
                    "username": it.username,
                    "title": pub.title if pub else (it.title or ""),
                    "description": pub.description if pub else (it.caption or ""),
                    "tags": ",".join(pub.tags) if pub and pub.tags else "",
                    "category_id": pub.category_id if pub else "22",
                    "privacy_status": pub.privacy_status if pub else "public",
                    "made_for_kids": pub.made_for_kids if pub else False,
                    "rights_confirmed": pub.rights_confirmed if pub else False,
                    "file_path": it.file_path or "",
                    "file_size": it.file_size or 0,
                    "sha256": it.sha256 or "",
                    "duration": it.duration or 0.0,
                    "download_timestamp": it.download_timestamp or "",
                }
            )

        fmt = export_format.lower().strip()
        if fmt == "csv":
            if not records:
                out.write_text("", encoding="utf-8")
                return out
            with out.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(records[0].keys()))
                writer.writeheader()
                writer.writerows(records)
        else:
            with out.open("w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, ensure_ascii=False)

        return out

    def import_directory(
        self,
        directory: Path,
        username: str = "local_import",
    ) -> dict[str, int]:
        """Scan a local directory for video files and catalog them in the library."""
        target_dir = Path(directory).resolve()
        if not target_dir.is_dir():
            raise NotADirectoryError(f"Directory not found: {target_dir}")

        supported_exts = {".mp4", ".mov", ".mkv", ".webm"}
        imported_count = 0
        skipped_count = 0

        for file_path in target_dir.rglob("*"):
            if not file_path.is_file() or file_path.suffix.lower() not in supported_exts:
                continue

            file_size = file_path.stat().st_size
            if file_size == 0:
                continue

            sha256 = compute_file_sha256(file_path)

            # Check duplicate by hash
            existing_hash = self.db.get_reel_by_hash(sha256)
            if existing_hash:
                skipped_count += 1
                continue

            media_id = f"local_{sha256[:12]}"
            # Look for optional JSON sidecar
            json_sidecar = file_path.with_suffix(".json")
            title = file_path.stem.replace("_", " ").title()
            caption = ""
            tags = []
            if json_sidecar.is_file():
                try:
                    with json_sidecar.open("r", encoding="utf-8") as jf:
                        meta_data = json.load(jf)
                        title = meta_data.get("title") or title
                        caption = meta_data.get("caption") or ""
                        if "tags" in meta_data and isinstance(meta_data["tags"], list):
                            tags = meta_data["tags"]
                except Exception:
                    pass

            now_iso = datetime.now(timezone.utc).isoformat()
            item = MediaItem(
                id=media_id,
                source_platform="local",
                source_id=file_path.name,
                username=username,
                title=title[:100],
                caption=caption,
                file_path=str(file_path),
                file_size=file_size,
                sha256=sha256,
                download_timestamp=now_iso,
            )
            self.db.upsert_media_item(item)

            pub_meta = PublishingMetadata(
                media_id=media_id,
                title=title[:100],
                description=caption,
                tags=tags,
                privacy_status="public",
            )
            self.db.upsert_publishing_metadata(pub_meta)

            self.db.upsert_profile(username, f"local://{username}")
            stub_reel = JobRecord(
                shortcode=media_id,
                username=username,
                profile_url=f"local://{username}",
                reel_url=f"file://{file_path}",
                status=DownloadStatus.COMPLETED,
                destination_path=str(file_path),
                file_size=file_size,
                sha256_hash=sha256,
                download_timestamp=now_iso,
                backend="local_import",
            )
            self.db.upsert_reel(stub_reel)

            # Record hash
            with self.db.transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO file_hashes (sha256, shortcode, file_path, file_size, first_seen)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(sha256) DO NOTHING
                    """,
                    (sha256, media_id, str(file_path), file_size, now_iso),
                )

            imported_count += 1

        return {"imported": imported_count, "skipped_duplicates": skipped_count}
