"""Metadata editing and YouTube validation rules for media library assets."""

from __future__ import annotations

from typing import Optional

from loopstash.database import Database
from loopstash.models import PublishingMetadata

YOUTUBE_MAX_TITLE_LENGTH = 100
YOUTUBE_MAX_DESCRIPTION_LENGTH = 5000
YOUTUBE_MAX_TOTAL_TAGS_LENGTH = 500
VALID_PRIVACY_STATUSES = {"public", "private", "unlisted"}


def validate_youtube_metadata(
    title: str,
    description: str = "",
    tags: Optional[list[str]] = None,
    privacy_status: str = "public",
) -> tuple[bool, list[str]]:
    """Validate publishing metadata against YouTube Data API v3 limits.

    Returns (is_valid, list_of_error_strings).
    """
    errors: list[str] = []

    clean_title = title.strip()
    if not clean_title:
        errors.append("Title cannot be empty.")
    elif len(clean_title) > YOUTUBE_MAX_TITLE_LENGTH:
        errors.append(
            f"Title exceeds maximum length of {YOUTUBE_MAX_TITLE_LENGTH} characters "
            f"({len(clean_title)} chars)."
        )

    if description and len(description) > YOUTUBE_MAX_DESCRIPTION_LENGTH:
        errors.append(
            f"Description exceeds maximum length of {YOUTUBE_MAX_DESCRIPTION_LENGTH} characters "
            f"({len(description)} chars)."
        )

    if tags:
        # YouTube limits total length of comma-separated tags
        joined_tags = ",".join(tags)
        if len(joined_tags) > YOUTUBE_MAX_TOTAL_TAGS_LENGTH:
            errors.append(
                f"Combined tags length exceeds {YOUTUBE_MAX_TOTAL_TAGS_LENGTH} characters "
                f"({len(joined_tags)} chars)."
            )

    if privacy_status.lower() not in VALID_PRIVACY_STATUSES:
        errors.append(
            f"Invalid privacy status '{privacy_status}'. "
            f"Allowed: {', '.join(sorted(VALID_PRIVACY_STATUSES))}."
        )

    return len(errors) == 0, errors


class MetadataEditor:
    """Service for validating, updating, and preserving editable publishing metadata."""

    def __init__(self, db: Database):
        self.db = db

    def edit(
        self,
        media_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[list[str]] = None,
        category_id: Optional[str] = None,
        privacy_status: Optional[str] = None,
        made_for_kids: Optional[bool] = None,
        thumbnail_path: Optional[str] = None,
        language: Optional[str] = None,
        rights_confirmed: Optional[bool] = None,
        rights_notes: Optional[str] = None,
    ) -> PublishingMetadata:
        """Apply edits to a media item's publishing metadata with API validation."""
        item = self.db.get_media_item(media_id)
        if not item:
            raise KeyError(f"Media item '{media_id}' not found in library.")

        current = self.db.get_publishing_metadata(media_id)
        if not current:
            current = PublishingMetadata(
                media_id=media_id,
                title=item.title or f"Media {media_id}",
                description=item.caption or "",
                tags=[],
            )

        new_title = title.strip() if title is not None else current.title
        new_desc = description if description is not None else current.description
        new_tags = tags if tags is not None else current.tags
        new_privacy = (
            privacy_status.lower().strip() if privacy_status is not None else current.privacy_status
        )

        is_valid, validation_errors = validate_youtube_metadata(
            title=new_title,
            description=new_desc,
            tags=new_tags,
            privacy_status=new_privacy,
        )
        if not is_valid:
            raise ValueError(f"Metadata validation failed: {'; '.join(validation_errors)}")

        updated_meta = PublishingMetadata(
            media_id=media_id,
            title=new_title,
            description=new_desc,
            tags=new_tags,
            category_id=category_id if category_id is not None else current.category_id,
            privacy_status=new_privacy,
            made_for_kids=made_for_kids if made_for_kids is not None else current.made_for_kids,
            thumbnail_path=(
                thumbnail_path if thumbnail_path is not None else current.thumbnail_path
            ),
            language=language if language is not None else current.language,
            rights_confirmed=(
                rights_confirmed if rights_confirmed is not None else current.rights_confirmed
            ),
            rights_notes=rights_notes if rights_notes is not None else current.rights_notes,
        )

        self.db.upsert_publishing_metadata(updated_meta)
        return updated_meta
