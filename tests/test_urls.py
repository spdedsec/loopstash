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
        (
            "https://www.instagram.com/creator/reel/CgK12345/",
            "points to a single post/reel",
        ),
        (
            "https://www.instagram.com/creator/reels/extra_segment/",
            "extra path segments",
        ),
        (
            "https://www.instagram.com/creator/random_page/",
            "archives reels only",
        ),
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


def test_reel_item_year_parsing():
    from loopstash.models import ReelItem

    # ISO string
    r1 = ReelItem("c1", "user", "url", upload_timestamp="2026-05-15T12:00:00Z")
    assert r1.get_year() == "2026"

    # Unix timestamp (numeric string: 1715774400 corresponds to year 2024)
    r2 = ReelItem("c2", "user", "url", upload_timestamp="1715774400")
    assert r2.get_year() == "2024"

    # Unix timestamp float string
    r3 = ReelItem("c3", "user", "url", upload_timestamp="1715774400.0")
    assert r3.get_year() == "2024"

    # Fallback to current year when timestamp is None
    r4 = ReelItem("c4", "user", "url", upload_timestamp=None)
    assert len(r4.get_year()) == 4


def test_config_browser_nullable(tmp_path):
    from loopstash.config import load_config, update_config_key

    cfg_file = tmp_path / "test_config.json"
    cfg = load_config(cfg_file)
    assert cfg.browser is None

    # Set to firefox
    cfg = update_config_key("browser", "firefox", cfg_file)
    assert cfg.browser == "firefox"

    # Set to none (case-insensitive)
    cfg = update_config_key("browser", "none", cfg_file)
    assert cfg.browser is None
