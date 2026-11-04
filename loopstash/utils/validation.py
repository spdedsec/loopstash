"""Validation utilities for Instagram profiles and URLs."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

# Instagram usernames: alphanumeric, dots, underscores, 1-30 chars.
# Cannot start or end with a dot, cannot contain consecutive dots.
_USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_](?:[a-zA-Z0-9_.]*[a-zA-Z0-9_])?$")

_RESERVED_PATHS = {
    "p",
    "reel",
    "reels",
    "tv",
    "stories",
    "explore",
    "direct",
    "accounts",
    "developer",
    "about",
    "legal",
    "terms",
    "privacy",
    "directory",
    "press",
    "jobs",
    "api",
    "graphql",
}


def validate_username(username: str) -> tuple[bool, Optional[str]]:
    """Validate a raw Instagram username string."""
    cleaned = username.strip().lstrip("@")
    if not cleaned:
        return False, "Username cannot be empty."
    if len(cleaned) > 30:
        return False, f"Username '{cleaned}' exceeds the 30-character Instagram limit."
    if ".." in cleaned:
        return False, f"Username '{cleaned}' cannot contain consecutive periods."
    if not _USERNAME_PATTERN.match(cleaned):
        return False, (
            f"Username '{cleaned}' contains invalid characters. "
            "Only letters, numbers, periods, and underscores are allowed."
        )
    if cleaned.lower() in _RESERVED_PATHS:
        return False, f"'{cleaned}' is a reserved Instagram endpoint, not a user profile."
    return True, None


def validate_instagram_url(
    input_str: str,
) -> tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """Validate an Instagram username or profile URL.

    Returns:
        (is_valid, username, normalized_profile_url, error_message)
    """
    if not input_str or not input_str.strip():
        return False, None, None, "Profile URL or username cannot be empty."

    trimmed = input_str.strip()

    # If it's a plain username or @username
    if not trimmed.startswith("http://") and not trimmed.startswith("https://"):
        cleaned = trimmed.lstrip("@")
        if "/" in cleaned or "\\" in cleaned:
            return False, None, None, f"Invalid username format: '{trimmed}'"
        is_valid, err = validate_username(cleaned)
        if not is_valid:
            return False, None, None, err
        return True, cleaned, f"https://www.instagram.com/{cleaned}/", None

    # Parse URL
    try:
        parsed = urlparse(trimmed)
    except Exception as e:
        return False, None, None, f"Malformed URL: {e}"

    hostname = (parsed.hostname or "").lower()
    if hostname not in ("instagram.com", "www.instagram.com"):
        return (
            False,
            None,
            None,
            f"Domain '{hostname}' is not supported. LOOPSTASH requires an instagram.com profile.",
        )

    # Process path
    # e.g. /username/ or /username/reels/
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    if not path_parts:
        return False, None, None, "URL does not specify an Instagram profile username."

    first_segment = path_parts[0]
    if first_segment.lower() in _RESERVED_PATHS:
        if first_segment.lower() in ("p", "reel", "tv"):
            return (
                False,
                None,
                None,
                f"URL '{trimmed}' points to a single post/reel, not a profile. "
                "LOOPSTASH requires a profile URL (e.g., https://www.instagram.com/username/).",
            )
        return (
            False,
            None,
            None,
            f"URL path '/{first_segment}/' is an Instagram system endpoint, not a profile.",
        )

    # If path has multiple segments, second segment should only be reels or empty
    if len(path_parts) > 1:
        second_segment = path_parts[1].lower()
        if second_segment in ("p", "reel", "tv"):
            return (
                False,
                None,
                None,
                f"URL '{trimmed}' points to a single post/reel, not a profile. "
                "LOOPSTASH requires a profile URL (e.g., https://www.instagram.com/username/).",
            )
        if second_segment not in ("reels", ""):
            return (
                False,
                None,
                None,
                f"URL specifies /{path_parts[1]}/. LOOPSTASH archives Reels only.",
            )
        if len(path_parts) > 2:
            return (
                False,
                None,
                None,
                f"URL path '{trimmed}' contains unsupported extra path segments.",
            )

    username = first_segment
    is_valid, err = validate_username(username)
    if not is_valid:
        return False, None, None, err

    normalized = f"https://www.instagram.com/{username}/"
    return True, username, normalized, None
