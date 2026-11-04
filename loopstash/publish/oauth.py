"""Official YouTube Data API v3 OAuth 2.0 authentication and token management."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import keyring
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from loopstash.database import Database
from loopstash.models import PublishingAccount
from loopstash.utils.paths import get_default_data_dir

logger = logging.getLogger(__name__)

KEYRING_SERVICE_NAME = "loopstash_youtube"
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def _get_token_dir() -> Path:
    """Directory for storing restricted token files when keyring is unavailable."""
    d = get_default_data_dir() / "oauth_tokens"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_token_to_keyring_or_file(channel_id: str, token_data: dict) -> str:
    """Persist OAuth token JSON to OS keyring, falling back to chmod 0600 file."""
    token_json = json.dumps(token_data)
    storage_type = "keyring"
    try:
        keyring.set_password(KEYRING_SERVICE_NAME, channel_id, token_json)
    except Exception as e:
        logger.debug("Keyring write unavailable (%s), using secure local file store", e)
        storage_type = "file"

    if storage_type == "file":
        token_file = _get_token_dir() / f"{channel_id}.json"
        token_file.write_text(token_json, encoding="utf-8")
        try:
            token_file.chmod(0o600)
        except OSError:
            pass

    return f"{storage_type}:{channel_id}"


def _load_token_from_storage(token_storage_key: str) -> Optional[dict]:
    """Retrieve saved token dictionary from keyring or secure file store."""
    if not token_storage_key:
        return None

    if token_storage_key.startswith("keyring:"):
        channel_id = token_storage_key.split(":", 1)[1]
        try:
            val = keyring.get_password(KEYRING_SERVICE_NAME, channel_id)
            if val:
                return json.loads(val)
        except Exception:
            pass
        # Fall back to file if keyring fails
        token_file = _get_token_dir() / f"{channel_id}.json"
        if token_file.is_file():
            try:
                return json.loads(token_file.read_text(encoding="utf-8"))
            except Exception:
                pass
    elif token_storage_key.startswith("file:"):
        channel_id = token_storage_key.split(":", 1)[1]
        token_file = _get_token_dir() / f"{channel_id}.json"
        if token_file.is_file():
            try:
                return json.loads(token_file.read_text(encoding="utf-8"))
            except Exception:
                pass
    return None


class YouTubeAuthManager:
    """Manages YouTube OAuth authorization, secure token storage, and client creation."""

    def __init__(self, db: Database):
        self.db = db

    def authorize_account(
        self,
        client_secrets_path: Path,
        manual: bool = False,
    ) -> PublishingAccount:
        """Run official OAuth 2.0 flow, verify YouTube channel, and save credentials."""
        secrets_file = Path(client_secrets_path).resolve()
        if not secrets_file.is_file():
            raise FileNotFoundError(
                f"OAuth client secrets file not found: {secrets_file}. "
                "Download your desktop client credentials JSON from Google Cloud Console."
            )

        flow = InstalledAppFlow.from_client_secrets_file(
            str(secrets_file),
            scopes=YOUTUBE_SCOPES,
        )

        if manual:
            creds = flow.run_console()
        else:
            try:
                creds = flow.run_local_server(port=0, prompt="consent")
            except Exception as e:
                logger.info("Local server auth failed (%s), falling back to console flow", e)
                creds = flow.run_console()

        youtube_client = build("youtube", "v3", credentials=creds)

        # Retrieve authorized channel info
        resp = youtube_client.channels().list(part="id,snippet", mine=True).execute()
        items = resp.get("items", [])
        if not items:
            raise RuntimeError(
                "No YouTube channel associated with the authorized Google account. "
                "Please ensure the account has an active YouTube channel."
            )

        channel_id = items[0]["id"]
        channel_title = items[0]["snippet"].get("title", f"Channel {channel_id}")
        custom_url = items[0]["snippet"].get("customUrl", "")
        account_name = f"{channel_title} ({custom_url})" if custom_url else channel_title

        token_data = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes,
        }

        storage_key = _save_token_to_keyring_or_file(channel_id, token_data)

        account = PublishingAccount(
            id=channel_id,
            platform="youtube",
            account_name=account_name,
            channel_id=channel_id,
            channel_title=channel_title,
            token_storage_key=storage_key,
            is_active=True,
        )
        self.db.upsert_publishing_account(account)
        return account

    def get_credentials(self, channel_id: str) -> Credentials:
        """Retrieve and automatically refresh OAuth credentials for a channel."""
        account = self.db.get_publishing_account(channel_id)
        if not account or not account.is_active:
            raise KeyError(f"Publishing account for channel '{channel_id}' not found or inactive.")

        token_data = _load_token_from_storage(account.token_storage_key)
        if not token_data:
            raise RuntimeError(
                f"Credentials for channel '{channel_id}' could not be loaded from storage. "
                "Please run 'loopstash publish auth youtube' to reconnect."
            )

        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id"),
            client_secret=token_data.get("client_secret"),
            scopes=token_data.get("scopes", YOUTUBE_SCOPES),
        )

        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                updated_token = {
                    "token": creds.token,
                    "refresh_token": creds.refresh_token,
                    "token_uri": creds.token_uri,
                    "client_id": creds.client_id,
                    "client_secret": creds.client_secret,
                    "scopes": creds.scopes,
                }
                _save_token_to_keyring_or_file(channel_id, updated_token)
            else:
                raise RuntimeError(
                    f"Credentials for channel '{channel_id}' are expired and cannot be refreshed. "
                    "Please re-authenticate."
                )

        return creds

    def build_service(self, channel_id: str) -> Resource:
        """Construct an authenticated Google API YouTube Resource."""
        creds = self.get_credentials(channel_id)
        return build("youtube", "v3", credentials=creds)

    def disconnect_account(self, channel_id: str) -> bool:
        """Disconnect and delete stored account credentials."""
        account = self.db.get_publishing_account(channel_id)
        if not account:
            return False

        try:
            keyring.delete_password(KEYRING_SERVICE_NAME, channel_id)
        except Exception:
            pass

        token_file = _get_token_dir() / f"{channel_id}.json"
        if token_file.is_file():
            token_file.unlink(missing_ok=True)

        self.db.delete_publishing_account(channel_id)
        return True
