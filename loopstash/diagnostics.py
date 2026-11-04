"""Diagnostic routines for LOOPSTASH doctor command."""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional

from loopstash import __version__
from loopstash.backends.gallery_dl import GalleryDlBackend, find_gallery_dl_binary
from loopstash.config import AppConfig, load_config
from loopstash.database import Database
from loopstash.models import DiagnosticCheck
from loopstash.utils.paths import ensure_directory, get_free_space


def check_python_version() -> DiagnosticCheck:
    """Validate that Python runtime meets the minimum >= 3.11 requirement."""
    major, minor, micro = sys.version_info[:3]
    version_str = f"{major}.{minor}.{micro}"
    if (major, minor) >= (3, 11):
        return DiagnosticCheck(
            name="Python Version",
            status="PASS",
            details=f"Python {version_str} ({sys.executable})",
        )
    return DiagnosticCheck(
        name="Python Version",
        status="FAIL",
        details=f"Python {version_str} is below required >= 3.11.",
        remediation="Upgrade Python to 3.11 or newer.",
    )


def check_loopstash_installation() -> DiagnosticCheck:
    """Check LOOPSTASH package integrity and location."""
    import loopstash

    pkg_path = Path(loopstash.__file__).parent
    return DiagnosticCheck(
        name="LOOPSTASH Package",
        status="PASS",
        details=f"v{__version__} installed at {pkg_path}",
    )


def check_gallery_dl() -> DiagnosticCheck:
    """Validate gallery-dl installation and version."""
    backend = GalleryDlBackend()
    is_avail, ver_or_err = backend.check_availability()
    bin_path = find_gallery_dl_binary()

    if is_avail:
        return DiagnosticCheck(
            name="Backend (gallery-dl)",
            status="PASS",
            details=f"gallery-dl {ver_or_err} ({bin_path})",
        )
    return DiagnosticCheck(
        name="Backend (gallery-dl)",
        status="FAIL",
        details=f"Not available: {ver_or_err}",
        remediation=(
            "Install gallery-dl via package manager ('sudo pacman -S gallery-dl') "
            "or 'pip install gallery-dl'."
        ),
    )


def check_ffmpeg() -> DiagnosticCheck:
    """Validate ffmpeg availability for media stream post-processing."""
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        try:
            res = subprocess.run(
                [ffmpeg_bin, "-version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            first_line = res.stdout.splitlines()[0] if res.stdout else "Available"
            return DiagnosticCheck(
                name="FFmpeg",
                status="PASS",
                details=f"{first_line} ({ffmpeg_bin})",
            )
        except Exception as e:
            return DiagnosticCheck(
                name="FFmpeg",
                status="WARN",
                details=f"Found at {ffmpeg_bin} but failed executing: {e}",
            )
    return DiagnosticCheck(
        name="FFmpeg",
        status="WARN",
        details="ffmpeg not found on PATH. Some high-res merged streams may be limited.",
        remediation="Install ffmpeg ('sudo pacman -S ffmpeg' or 'apt install ffmpeg').",
    )


def check_dependencies() -> DiagnosticCheck:
    """Validate core Python dependencies are importable."""
    required = [
        "typer",
        "rich",
        "platformdirs",
        "gallery_dl",
        "googleapiclient",
        "google.auth",
        "keyring",
    ]
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            if pkg == "gallery_dl" and find_gallery_dl_binary():
                continue
            missing.append(pkg)

    if not missing:
        return DiagnosticCheck(
            name="Dependencies",
            status="PASS",
            details="All core libraries (typer, rich, google-api, keyring, etc.) present.",
        )
    return DiagnosticCheck(
        name="Dependencies",
        status="FAIL",
        details=f"Missing dependencies: {', '.join(missing)}",
        remediation=f"Run: pip install {' '.join(missing)}",
    )


def check_database_health(db: Optional[Database] = None) -> DiagnosticCheck:
    """Validate SQLite database schema and perform PRAGMA integrity_check."""
    own_db = False
    if db is None:
        try:
            db = Database()
            own_db = True
        except Exception as e:
            return DiagnosticCheck(
                name="Database Health",
                status="FAIL",
                details=f"Failed initializing database: {e}",
                remediation="Check data directory permissions.",
            )

    try:
        ok = db.verify_integrity()
        if ok:
            stats = db.get_statistics()
            media_count = db.count_media_items()
            return DiagnosticCheck(
                name="Database Health",
                status="PASS",
                details=(
                    f"SQLite OK (WAL mode). Tracked reels: {stats['total_reels']}, "
                    f"library items: {media_count}, profiles: {stats['total_profiles']}."
                ),
            )
        return DiagnosticCheck(
            name="Database Health",
            status="FAIL",
            details="SQLite database integrity check reported errors.",
            remediation="Database may be corrupted. Backup and recreate.",
        )
    finally:
        if own_db and db:
            db.close()


def check_youtube_accounts(db: Optional[Database] = None) -> DiagnosticCheck:
    """Check if any YouTube channels are authorized for publishing."""
    own_db = False
    if db is None:
        try:
            db = Database()
            own_db = True
        except Exception:
            return DiagnosticCheck(
                name="YouTube Accounts",
                status="WARN",
                details="Could not inspect database.",
            )

    try:
        accounts = db.get_all_publishing_accounts(active_only=True)
        if accounts:
            names = [a.channel_title for a in accounts]
            return DiagnosticCheck(
                name="YouTube Accounts",
                status="PASS",
                details=f"Connected channels: {', '.join(names)}",
            )
        return DiagnosticCheck(
            name="YouTube Accounts",
            status="WARN",
            details="No YouTube channel connected.",
            remediation="Run 'loopstash publish auth youtube' to connect a channel.",
        )
    finally:
        if own_db and db:
            db.close()


def check_storage_permissions(config: Optional[AppConfig] = None) -> DiagnosticCheck:
    """Verify write permissions and disk space for the target storage directory."""
    cfg = config or load_config()
    target_dir = Path(cfg.download_dir)

    try:
        ensure_directory(target_dir)
        test_file = target_dir / ".loopstash_write_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)

        free_bytes = get_free_space(target_dir)
        free_mb = free_bytes // (1024 * 1024)

        if free_mb < cfg.min_free_space_mb:
            return DiagnosticCheck(
                name="Storage & Disk",
                status="WARN",
                details=(
                    f"Write OK at '{target_dir}'. Low disk space: {free_mb} MB available "
                    f"(configured min: {cfg.min_free_space_mb} MB)."
                ),
                remediation=(
                    "Free disk space or change download directory via "
                    "'loopstash config --set download_dir=...'."
                ),
            )

        return DiagnosticCheck(
            name="Storage & Disk",
            status="PASS",
            details=f"Write OK at '{target_dir}'. {free_mb} MB free space available.",
        )
    except PermissionError:
        return DiagnosticCheck(
            name="Storage & Disk",
            status="FAIL",
            details=f"Permission denied writing to '{target_dir}'.",
            remediation=f"Ensure write permissions for '{target_dir}' or choose another folder.",
        )
    except Exception as e:
        return DiagnosticCheck(
            name="Storage & Disk",
            status="FAIL",
            details=f"Storage check failed at '{target_dir}': {e}",
            remediation="Check path validity and directory permissions.",
        )


def check_network_connectivity() -> DiagnosticCheck:
    """Validate external DNS resolution and HTTPS connectivity."""
    host = "www.instagram.com"
    try:
        ip = socket.gethostbyname(host)
        sock = socket.create_connection((ip, 443), timeout=5.0)
        sock.close()
        return DiagnosticCheck(
            name="Network Connectivity",
            status="PASS",
            details=f"Resolved {host} -> {ip}:443 reachable.",
        )
    except socket.gaierror as e:
        return DiagnosticCheck(
            name="Network Connectivity",
            status="FAIL",
            details=f"DNS resolution failed for {host}: {e}",
            remediation="Check your internet connection and DNS settings.",
        )
    except Exception as e:
        return DiagnosticCheck(
            name="Network Connectivity",
            status="WARN",
            details=f"Could not connect to {host}:443 ({e}).",
            remediation="Check internet connection, proxy, or firewall settings.",
        )


def run_all_diagnostics(
    db: Optional[Database] = None, config: Optional[AppConfig] = None
) -> list[DiagnosticCheck]:
    """Execute complete diagnostic suite."""
    return [
        check_python_version(),
        check_loopstash_installation(),
        check_gallery_dl(),
        check_ffmpeg(),
        check_dependencies(),
        check_database_health(db),
        check_youtube_accounts(db),
        check_storage_permissions(config),
        check_network_connectivity(),
    ]
