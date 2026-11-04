"""Tests for URL and username validation."""

from __future__ import annotations

import pytest

from loopstash.utils.validation import validate_instagram_url, validate_username


@pytest.mark.parametrize(
    "raw_input,expected_user,expected_url",
    [
        ("creator_name", "creator_name", "https://www.instagram.com/creator_name/"),
        ("@creator_name", "creator_name", "https://www.instagram.com/creator_name/"),
        ("  @creator_name  ", "creator_name", "https://www.instagram.com/creator_name/"),
        (
            "https://www.instagram.com/creator_name",
            "creator_name",
            "https://www.instagram.com/creator_name/",
        ),
        (
            "https://www.instagram.com/creator_name/",
            "creator_name",
            "https://www.instagram.com/creator_name/",
        ),
        (
            "http://instagram.com/creator_name/",
            "creator_name",
            "https://www.instagram.com/creator_name/",
        ),
        (
            "https://www.instagram.com/creator_name/reels",
            "creator_name",
            "https://www.instagram.com/creator_name/",
        ),
        (
            "https://www.instagram.com/creator_name/reels/",
            "creator_name",
            "https://www.instagram.com/creator_name/",
        ),
        (
            "https://www.instagram.com/creator.with.dots/",
            "creator.with.dots",
            "https://www.instagram.com/creator.with.dots/",
        ),
        (
            "https://www.instagram.com/user_123/?hl=en&theme=dark",
            "user_123",
            "https://www.instagram.com/user_123/",
        ),
        ("https://instagram.com/user_123#reels", "user_123", "https://www.instagram.com/user_123/"),
    ],
)
def test_valid_profile_urls(raw_input: str, expected_user: str, expected_url: str):
    is_valid, username, normalized, err = validate_instagram_url(raw_input)
    assert is_valid is True
    assert err is None
    assert username == expected_user
    assert normalized == expected_url


@pytest.mark.parametrize(
    "invalid_input,expected_substr",
    [
        ("", "cannot be empty"),
        ("   ", "cannot be empty"),
        ("user..name", "consecutive periods"),
        ("https://www.instagram.com/user..name/", "consecutive periods"),
        ("a" * 31, "exceeds the 30-character"),
        ("invalid user with spaces", "invalid"),
        ("user@name", "invalid characters"),
        ("https://tiktok.com/@creator", "Domain 'tiktok.com' is not supported"),
        ("https://youtube.com/c/creator", "Domain 'youtube.com' is not supported"),
        ("https://www.instagram.com/", "does not specify an Instagram profile"),
        ("https://www.instagram.com/p/CgK12345/", "points to a single post/reel"),
        ("https://www.instagram.com/reel/CgK12345/", "points to a single post/reel"),
        ("https://www.instagram.com/explore/", "system endpoint"),
        ("https://www.instagram.com/accounts/", "system endpoint"),
        ("https://www.instagram.com/username/tagged/", "LOOPSTASH archives Reels only"),
    ],
)
def test_invalid_profile_urls(invalid_input: str, expected_substr: str):
    is_valid, username, normalized, err = validate_instagram_url(invalid_input)
    assert is_valid is False
    assert username is None
    assert normalized is None
    assert err is not None
    assert expected_substr.lower() in err.lower()


def test_validate_username_rules():
    assert validate_username("valid_user.1")[0] is True
    assert validate_username("")[0] is False
    assert validate_username("a" * 35)[0] is False
    assert validate_username("reels")[0] is False  # Reserved
    assert validate_username("explore")[0] is False  # Reserved
