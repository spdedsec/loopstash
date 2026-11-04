"""Tests for YouTube OAuth 2.0 flow, token storage, refresh, and disconnect."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from google.oauth2.credentials import Credentials

from loopstash.database import Database
from loopstash.publish.oauth import YouTubeAuthManager


@pytest.fixture
def auth_db(tmp_path: Path) -> Database:
    db_file = tmp_path / "test_oauth.db"
    return Database(db_file)


def test_authorize_account_success(auth_db: Database, tmp_path: Path):
    """Test successful OAuth authorization flow and account persistence."""
    manager = YouTubeAuthManager(auth_db)

    # Create dummy client secrets JSON
    client_secrets = tmp_path / "client_secret.json"
    client_secrets.write_text(
        '{"installed": {"client_id": "cid", "client_secret": "csec"}}', encoding="utf-8"
    )

    # Mock InstalledAppFlow
    mock_flow = MagicMock()
    mock_creds = MagicMock(spec=Credentials)
    mock_creds.token = "fake_access_token_123"
    mock_creds.refresh_token = "fake_refresh_token_456"
    mock_creds.token_uri = "https://oauth2.googleapis.com/token"
    mock_creds.client_id = "test_client_id"
    mock_creds.client_secret = "test_client_secret"
    mock_creds.scopes = ["https://www.googleapis.com/auth/youtube.upload"]
    mock_flow.run_local_server.return_value = mock_creds

    # Mock YouTube API response for channels().list()
    mock_youtube = MagicMock()
    mock_channels = MagicMock()
    mock_channels.list.return_value.execute.return_value = {
        "items": [
            {
                "id": "UC_TEST_CHANNEL_123",
                "snippet": {
                    "title": "LOOPSTASH Official Channel",
                    "customUrl": "@loopstash_official",
                },
            }
        ]
    }
    mock_youtube.channels.return_value = mock_channels

    with (
        patch(
            "loopstash.publish.oauth.InstalledAppFlow.from_client_secrets_file",
            return_value=mock_flow,
        ),
        patch("loopstash.publish.oauth.build", return_value=mock_youtube),
    ):
        account = manager.authorize_account(client_secrets)

    assert account.channel_id == "UC_TEST_CHANNEL_123"
    assert "LOOPSTASH Official Channel" in account.channel_title
    assert account.is_active is True

    # Verify persisted in database
    db_account = auth_db.get_publishing_account("UC_TEST_CHANNEL_123")
    assert db_account is not None
    assert db_account.channel_id == "UC_TEST_CHANNEL_123"
    assert db_account.channel_title == "LOOPSTASH Official Channel"


def test_get_credentials_auto_refresh_when_expired(auth_db: Database, tmp_path: Path):
    """Test that expired credentials trigger automatic token refresh."""
    manager = YouTubeAuthManager(auth_db)

    # Directly create account with file storage
    channel_id = "UC_REFRESH_TEST"
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    token_file = token_dir / f"{channel_id}.json"
    token_json_str = (
        '{"token": "old_token", "refresh_token": "valid_refresh", '
        '"client_id": "cid", "client_secret": "csec"}'
    )
    token_file.write_text(token_json_str, encoding="utf-8")

    from loopstash.models import PublishingAccount

    acc = PublishingAccount(
        id=channel_id,
        channel_id=channel_id,
        channel_title="Refresh Channel",
        token_storage_key=f"file:{channel_id}",
    )
    auth_db.upsert_publishing_account(acc)

    with (
        patch("loopstash.publish.oauth._get_token_dir", return_value=token_dir),
        patch.object(Credentials, "valid", False),
        patch.object(Credentials, "expired", True),
        patch.object(Credentials, "refresh") as mock_refresh,
    ):
        creds = manager.get_credentials(channel_id)
        assert creds is not None
        mock_refresh.assert_called_once()


def test_disconnect_account(auth_db: Database, tmp_path: Path):
    """Test disconnecting account removes record and deletes token file."""
    manager = YouTubeAuthManager(auth_db)
    channel_id = "UC_DISCONNECT_TEST"

    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    token_file = token_dir / f"{channel_id}.json"
    token_file.write_text("{}", encoding="utf-8")

    from loopstash.models import PublishingAccount

    acc = PublishingAccount(
        id=channel_id,
        channel_id=channel_id,
        channel_title="Disconnect Channel",
        token_storage_key=f"file:{channel_id}",
    )
    auth_db.upsert_publishing_account(acc)

    with patch("loopstash.publish.oauth._get_token_dir", return_value=token_dir):
        success = manager.disconnect_account(channel_id)

    assert success is True
    assert not token_file.exists()
    assert auth_db.get_publishing_account(channel_id) is None
