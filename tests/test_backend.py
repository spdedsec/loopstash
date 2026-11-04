"""Tests for backend abstraction, gallery-dl error classification, and reels extraction parsing."""

from __future__ import annotations

import json

import pytest

from loopstash.backends.base import (
    AuthenticationRequiredError,
    BackendError,
    BackendNotFoundError,
    NetworkError,
    ProfileNotFoundError,
    RateLimitError,
)
from loopstash.backends.gallery_dl import GalleryDlBackend, find_gallery_dl_binary


def test_backend_missing_binary():
    """Test handling when gallery-dl executable is missing."""
    backend = GalleryDlBackend(binary_path="/nonexistent/path/to/gallery-dl")
    is_avail, msg = backend.check_availability()
    assert is_avail is False
    assert "not found" in msg.lower() or "failed" in msg.lower()

    with pytest.raises(BackendNotFoundError):
        backend.extract_reels("testuser", "https://instagram.com/testuser/")


def test_gallery_dl_version_check():
    """Test version checking with installed or available gallery-dl binary."""
    binary = find_gallery_dl_binary()
    if binary:
        backend = GalleryDlBackend(binary_path=binary)
        is_avail, ver = backend.check_availability()
        assert is_avail is True
        assert len(ver) > 0


def test_error_classification():
    """Test classification of stderr output from gallery-dl."""
    backend = GalleryDlBackend()

    # 404 / User not found
    err404 = backend._classify_error("HTTP Error 404: Not Found", 1)
    assert isinstance(err404, ProfileNotFoundError)
    assert not err404.retriable

    err_no_user = backend._classify_error("ERROR: User 'unknown_user_123' does not exist", 1)
    assert isinstance(err_no_user, ProfileNotFoundError)

    # 429 / Rate limit
    err429 = backend._classify_error("HTTP Error 429: Too Many Requests. retry-after: 45", 1)
    assert isinstance(err429, RateLimitError)
    assert err429.retry_after == 45
    assert err429.retriable

    # Authentication / Login required
    err_auth = backend._classify_error("Redirected to login. checkpoint_required", 1)
    assert isinstance(err_auth, AuthenticationRequiredError)
    assert not err_auth.retriable

    err_private = backend._classify_error("This account is private", 1)
    assert isinstance(err_private, AuthenticationRequiredError)

    # Network / Timeout
    err_net = backend._classify_error(
        "HTTPSConnectionPool: Max retries exceeded (Caused by TimeoutError)", 1
    )
    assert isinstance(err_net, NetworkError)
    assert err_net.retriable

    # Generic backend error
    err_other = backend._classify_error("Unknown syntax issue in command", 2)
    assert isinstance(err_other, BackendError)
    assert err_other.retriable


def test_parse_extraction_json_filters_non_reels():
    """Test that json parser extracts only reels and filters out photos/stories/avatars."""
    backend = GalleryDlBackend()

    sample_output = [
        # 1. Avatar (must be filtered out)
        [
            2,
            "https://avatar.url",
            {"subcategory": "avatar", "post_url": "https://instagram.com/avatar"},
        ],
        # 2. Photo post (must be filtered out)
        [
            2,
            "https://photo.url",
            {"subcategory": "photos", "post_url": "https://instagram.com/p/PHOTO1/"},
        ],
        # 3. Real Reel post
        [
            2,
            "https://cdn.instagram.com/reel1.mp4",
            {
                "subcategory": "reels",
                "shortcode": "REEL_001",
                "post_url": "https://www.instagram.com/reel/REEL_001/",
                "media_id": "123456789",
                "title": "Summer Vibes Reel",
                "description": "Fun at the beach #summer",
                "date": "2026-06-01 12:00:00",
                "video_url": "https://cdn.instagram.com/reel1.mp4",
                "video_duration": 30.5,
                "width": 1080,
                "height": 1920,
            },
        ],
        # 4. Another Reel without explicit subcategory but with /reel/ in post_url
        [
            2,
            "https://cdn.instagram.com/reel2.mp4",
            {
                "subcategory": "",
                "post_url": "https://www.instagram.com/reel/REEL_002/",
                "date": "2026-07-04",
                "video_url": "https://cdn.instagram.com/reel2.mp4",
            },
        ],
        # 5. Duplicate of reel 1
        [
            2,
            "https://cdn.instagram.com/reel1_dup.mp4",
            {
                "subcategory": "reels",
                "shortcode": "REEL_001",
                "post_url": "https://www.instagram.com/reel/REEL_001/",
            },
        ],
    ]

    raw_json = json.dumps(sample_output)
    reels = backend._parse_extraction_json(
        raw_json=raw_json,
        username="beach_creator",
        profile_url="https://www.instagram.com/beach_creator/",
    )

    assert len(reels) == 2
    assert reels[0].shortcode == "REEL_001"
    assert reels[0].username == "beach_creator"
    assert reels[0].media_id == "123456789"
    assert reels[0].duration == 30.5
    assert reels[0].title == "Summer Vibes Reel"

    assert reels[1].shortcode == "REEL_002"
    assert reels[1].reel_url == "https://www.instagram.com/reel/REEL_002/"
