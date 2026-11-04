"""Pytest shared fixtures and mock factories for LOOPSTASH."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Generator

import pytest

from loopstash.backends.base import BaseBackend
from loopstash.database import Database
from loopstash.models import ReelItem
from loopstash.storage import StorageManager


class MockExtractionBackend(BaseBackend):
    """Predictable mock backend for testing without external network access."""

    def __init__(
        self, reels: list[ReelItem] = None, should_fail: bool = False, fail_error: Exception = None
    ):
        self._reels = reels if reels is not None else []
        self.should_fail = should_fail
        self.fail_error = fail_error
        self.downloaded_count = 0
        self.download_attempts = 0

    @property
    def name(self) -> str:
        return "mock-backend"

    def check_availability(self) -> tuple[bool, str]:
        if self.should_fail:
            return False, "Mock backend failure"
        return True, "1.0.0-mock"

    def extract_reels(
        self, username: str, profile_url: str, browser: str = None, max_items: int = None
    ) -> list[ReelItem]:
        if self.should_fail:
            raise self.fail_error or RuntimeError("Mock extraction failure")
        results = [r for r in self._reels if r.username == username or not r.username]
        if max_items:
            results = results[:max_items]
        return results

    def download_reel(
        self, reel: ReelItem, target_file: Path, temp_file: Path, browser: str = None
    ) -> Path:
        self.download_attempts += 1
        if self.should_fail:
            raise self.fail_error or RuntimeError("Mock download failure")

        temp_file.parent.mkdir(parents=True, exist_ok=True)
        # Write dummy mp4 video payload
        fake_content = f"DUMMY_MP4_DATA_FOR_{reel.shortcode}".encode("utf-8")
        temp_file.write_bytes(fake_content)
        self.downloaded_count += 1
        return temp_file


@pytest.fixture
def tmp_dir() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def test_db(tmp_dir: Path) -> Generator[Database, None, None]:
    db_file = tmp_dir / "test_loopstash.db"
    db = Database(db_file)
    yield db
    db.close()


@pytest.fixture
def test_storage(tmp_dir: Path) -> StorageManager:
    return StorageManager(tmp_dir / "archive")


@pytest.fixture
def sample_reel() -> ReelItem:
    return ReelItem(
        shortcode="CwK12345XYZ",
        username="testcreator",
        reel_url="https://www.instagram.com/reel/CwK12345XYZ/",
        media_id="3000111222",
        profile_url="https://www.instagram.com/testcreator/",
        title="Test Reel Title",
        caption="A fantastic nature clip #nature #reels",
        upload_timestamp="2026-05-15T10:30:00Z",
        video_url="https://cdn.instagram.com/v/test.mp4",
        duration=15.5,
        width=1080,
        height=1920,
    )
