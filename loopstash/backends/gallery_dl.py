"""Gallery-dl extraction backend implementation for LOOPSTASH."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from loopstash.backends.base import (
    AuthenticationRequiredError,
    BackendError,
    BackendNotFoundError,
    BaseBackend,
    DownloadError,
    NetworkError,
    ProfileNotFoundError,
    RateLimitError,
)
from loopstash.models import ReelItem

logger = logging.getLogger(__name__)


def find_gallery_dl_binary() -> Optional[str]:
    """Locate the gallery-dl binary in venv or system PATH."""
    prefix = Path(sys.prefix)
    candidates = [
        prefix / "bin" / "gallery-dl",
        prefix / "Scripts" / "gallery-dl.exe",
        prefix / "gallery-dl.exe",
    ]
    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)

    which_path = shutil.which("gallery-dl")
    if which_path:
        return which_path

    return None


class GalleryDlBackend(BaseBackend):
    """Instagram Reels extraction backend powered by gallery-dl."""

    def __init__(self, binary_path: Optional[str] = None):
        self._binary = binary_path or find_gallery_dl_binary()

    @property
    def name(self) -> str:
        return "gallery-dl"

    def check_availability(self) -> tuple[bool, str]:
        """Check if gallery-dl is installed and executable."""
        binary = self._binary or find_gallery_dl_binary()
        if not binary or not (shutil.which(binary) or Path(binary).is_file()):
            return False, "gallery-dl executable not found on PATH or in virtual environment."

        try:
            res = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if res.returncode == 0:
                ver = res.stdout.strip()
                return True, ver
            return False, f"gallery-dl returned code {res.returncode}: {res.stderr.strip()}"
        except Exception as e:
            return False, f"Failed to execute gallery-dl: {e}"

    def _classify_error(self, stderr: str, returncode: int) -> BackendError:
        """Analyze gallery-dl stderr output and return appropriate classified exception."""
        err_lower = stderr.lower()

        # 404 / User not found
        if any(
            term in err_lower
            for term in ["404", "user not found", "profile not found", "does not exist"]
        ):
            return ProfileNotFoundError(f"Instagram profile not found: {stderr.strip()}")

        # 429 / Rate Limit
        if any(term in err_lower for term in ["429", "too many requests", "rate limit", "throttl"]):
            retry_after = None
            m = re.search(r"retry[- ]after[:\s]+(\d+)", err_lower)
            if m:
                try:
                    retry_after = int(m.group(1))
                except ValueError:
                    pass
            return RateLimitError(
                f"Instagram rate limit reached (HTTP 429). {stderr.strip()}",
                retry_after=retry_after,
            )

        # Login required / Private profile / Checkpoint
        if any(
            term in err_lower
            for term in [
                "login required",
                "checkpoint_required",
                "challenge_required",
                "redirected to login",
                "this account is private",
                "private profile",
            ]
        ):
            return AuthenticationRequiredError(
                "Instagram requires authentication or account is private. "
                "Use --browser <browser> to provide session cookies."
            )

        # Network failures
        if any(
            term in err_lower
            for term in [
                "connection error",
                "connectionrefused",
                "connectionreset",
                "timeout",
                "timed out",
                "nameresolutionerror",
                "getaddrinfo failed",
                "max retries exceeded",
            ]
        ):
            return NetworkError(f"Network error during Instagram connection: {stderr.strip()}")

        return BackendError(
            f"Extraction backend error (exit {returncode}): {stderr.strip() or 'Unknown error'}",
            retriable=True,
        )

    def extract_reels(
        self,
        username: str,
        profile_url: str,
        browser: Optional[str] = None,
        max_items: Optional[int] = None,
    ) -> list[ReelItem]:
        """Discover all accessible Reels for the given Instagram profile."""
        binary = self._binary or find_gallery_dl_binary()
        if not binary or not (shutil.which(binary) or Path(binary).is_file()):
            raise BackendNotFoundError("gallery-dl binary not found.")

        # Target the reels endpoint directly
        target_url = f"https://www.instagram.com/{username}/reels/"

        cmd = [
            binary,
            "-j",
            "--simulate",
            "-o",
            "extractor.instagram.include=reels",
            "-o",
            "extractor.instagram.videos=true",
            "-o",
            "extractor.instagram.previews=false",
            "-o",
            "extractor.instagram.audio=false",
            target_url,
        ]

        if browser:
            cmd.extend(["--cookies-from-browser", browser])

        if max_items:
            cmd.extend(["-o", f"extractor.instagram.max-posts={max_items}"])

        logger.debug("Executing gallery-dl extract: %s", " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise NetworkError("Extraction timed out waiting for Instagram.") from exc
        except Exception as e:
            raise BackendError(f"Failed to run gallery-dl: {e}", retriable=True) from e

        if proc.returncode != 0 and not proc.stdout.strip():
            raise self._classify_error(proc.stderr, proc.returncode)

        # Parse output JSON
        raw_output = proc.stdout.strip()
        if not raw_output:
            # If exit code was 0 but no output, profile might have 0 reels
            if proc.returncode == 0:
                return []
            raise self._classify_error(proc.stderr, proc.returncode)

        return self._parse_extraction_json(raw_output, username, profile_url)

    def _parse_extraction_json(
        self, raw_json: str, username: str, profile_url: str
    ) -> list[ReelItem]:
        """Parse raw JSON output from gallery-dl into ReelItem list."""
        items: list[ReelItem] = []
        seen_shortcodes: set[str] = set()

        # gallery-dl dump could be a single JSON array or newline-delimited JSON
        data_blocks = []
        if raw_json.startswith("[") and raw_json.endswith("]"):
            try:
                parsed = json.loads(raw_json)
                if isinstance(parsed, list):
                    data_blocks = parsed
            except Exception:
                pass

        if not data_blocks:
            for line in raw_json.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data_blocks.append(json.loads(line))
                except Exception:
                    continue

        for block in data_blocks:
            metadata: dict = {}
            if isinstance(block, list) and len(block) >= 3:
                # Format: [msg_type, url, kwdict]
                _msg_type, _url, kwdict = block[0], block[1], block[2]
                if isinstance(kwdict, dict):
                    metadata = kwdict
            elif isinstance(block, dict):
                metadata = block

            if not metadata:
                continue

            # Check if this record is a Reel
            subcat = metadata.get("subcategory", "")
            post_url = metadata.get("post_url", "")
            is_reel = (
                subcat in ("reels", "reel")
                or "/reel/" in post_url
                or "/reels/" in post_url
                or metadata.get("video_url") is not None
            )

            # Ignore non-reels (photos, avatars, stories)
            if not is_reel and subcat in ("avatar", "stories", "photos", "tagged", "info"):
                continue

            shortcode = metadata.get("shortcode") or metadata.get("post_id") or metadata.get("code")
            if not shortcode:
                # Try to extract shortcode from post_url
                if post_url:
                    match = re.search(r"/(?:reel|p)/([^/?#]+)", post_url)
                    if match:
                        shortcode = match.group(1)

            if not shortcode or shortcode in seen_shortcodes:
                continue

            seen_shortcodes.add(shortcode)

            reel_url = post_url or f"https://www.instagram.com/reel/{shortcode}/"
            media_id = metadata.get("media_id")
            if media_id is not None:
                media_id = str(media_id)

            date_val = metadata.get("date")
            upload_timestamp = str(date_val) if date_val else None

            item = ReelItem(
                shortcode=shortcode,
                username=username,
                reel_url=reel_url,
                media_id=media_id,
                profile_url=profile_url,
                title=metadata.get("title"),
                caption=metadata.get("description") or metadata.get("caption"),
                upload_timestamp=upload_timestamp,
                video_url=metadata.get("video_url"),
                thumbnail_url=metadata.get("display_url"),
                duration=metadata.get("video_duration"),
                width=metadata.get("width"),
                height=metadata.get("height"),
                extra_metadata=metadata,
            )
            items.append(item)

        return items

    def download_reel(
        self,
        reel: ReelItem,
        target_file: Path,
        temp_file: Path,
        browser: Optional[str] = None,
    ) -> Path:
        """Download a single Reel video file to temp_file."""
        binary = self._binary or find_gallery_dl_binary()
        if not binary or not (shutil.which(binary) or Path(binary).is_file()):
            raise BackendNotFoundError("gallery-dl binary not found.")

        temp_dir = temp_file.parent
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Download specifically to temp_dir with filename matching temp_file.name
        cmd = [
            binary,
            "--directory",
            str(temp_dir),
            "--filename",
            f"{temp_file.stem}.{{extension}}",
            "-o",
            "extractor.instagram.videos=true",
            "-o",
            "extractor.instagram.previews=false",
            "-o",
            "extractor.instagram.audio=false",
            reel.reel_url,
        ]

        if browser:
            cmd.extend(["--cookies-from-browser", browser])

        logger.debug("Downloading reel %s: %s", reel.shortcode, " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise NetworkError(f"Download timed out for Reel '{reel.shortcode}'.") from exc
        except Exception as e:
            raise DownloadError(f"Failed to execute download subprocess: {e}") from e

        if proc.returncode != 0:
            raise self._classify_error(proc.stderr, proc.returncode)

        # Find the downloaded file in temp_dir matching stem
        valid_exts = (".mp4", ".mkv", ".webm", ".mov")
        matches = [
            f
            for f in temp_dir.glob(f"{temp_file.stem}.*")
            if f.is_file() and f.suffix.lower() in valid_exts and f.stat().st_size > 0
        ]
        if not matches:
            # Look for any newly created video in temp_dir
            for cand in temp_dir.iterdir():
                if cand.is_file() and cand.suffix.lower() in valid_exts and cand.stat().st_size > 0:
                    matches.append(cand)
                    break

        if not matches:
            err_details = proc.stderr.strip()
            raise DownloadError(
                f"No video file produced for Reel '{reel.shortcode}'. Stderr: {err_details}"
            )

        return matches[0]
