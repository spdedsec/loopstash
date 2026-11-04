# LOOPSTASH

> **Collect. Preserve. Organise.**  
> *A lightweight, reliable, open-source media archiving utility.*

[![CI](https://github.com/spdedsec/loopstash/actions/workflows/ci.yml/badge.svg)](https://github.com/spdedsec/loopstash/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

---

## ⚡ Overview

**LOOPSTASH** is a terminal-first media archiving utility built for reliability, data preservation, and speed. It discovers and downloads accessible **Instagram Reels** from public profiles in bulk without requiring you to open or click individual links.

### Strict Scope
- **Instagram Reels only**: Bulk archives all accessible public profile Reels.
- **Original Quality**: Preserves the original downloaded video stream without re-encoding, transcoding, or quality loss.
- **Excluded**: Regular photo posts, carousels, Stories, and highlights are intentionally omitted.

---

## ✨ Features

- **Dual-Layer Duplicate Detection**:
  - **Reel-Level**: Tracks Reel shortcodes and IDs in SQLite. Subsequent runs automatically skip already archived Reels.
  - **File-Level**: Generates SHA-256 hashes upon download to prevent duplicate file storage and safely resolve naming collisions.
- **Crash Recovery & Resume**:
  - Interrupted downloads survive network drops, computer restarts, and crashes.
  - Run `loopstash resume` to pick up exactly where you left off.
- **Smart Bounded Retries**:
  - Exponential backoff with randomized jitter handles transient network glitches and HTTP 5xx errors.
  - Graceful handling of HTTP 429 rate limits respecting `Retry-After`.
- **Privacy-First & Secure**:
  - No passwords required or collected.
  - Optional session authentication via local browser cookies (`--browser firefox`).
  - Zero telemetry, zero analytics, zero background daemons.
- **Cross-Platform Storage**:
  - Works seamlessly on Arch Linux / Omarchy, Ubuntu, Windows 10/11, and macOS.
  - Full support for external HDDs, spaces in paths, and Unicode directory names.
- **Built-in Diagnostics**:
  - Run `loopstash doctor` to verify environment health, dependency availability, storage permissions, and database integrity.

---

## 🚀 Quick Start

### 1. Installation

Requires **Python 3.11+**, `gallery-dl`, and `ffmpeg`.

```bash
# Clone the repository
git clone https://github.com/spdedsec/loopstash.git ~/loopstash
cd ~/loopstash

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install LOOPSTASH in editable mode with development tools
pip install -e ".[dev]"

# Optional: Symlink binary to user PATH
ln -sf ~/.local/share/loopstash/loopstash ~/.local/bin/loopstash
```

For platform-specific instructions (Arch Linux, Ubuntu, Windows, macOS), see [INSTALL.md](INSTALL.md).

### 2. Verify Installation

```bash
loopstash doctor
```

---

## 💻 CLI Usage

### Bulk Download Profile Reels

Provide an Instagram username or public profile URL:

```bash
# By username
loopstash download @natgeo

# By public URL
loopstash download "https://www.instagram.com/natgeo/"

# Specify custom destination directory (e.g., External HDD)
loopstash download @natgeo --output "/media/storage/Instagram_Archive"

# Use browser session cookies for age-restricted or login-prompted profiles
loopstash download @natgeo --browser firefox

# Force re-download of already archived items
loopstash download @natgeo --force

# Preview discovered Reels without downloading
loopstash download @natgeo --dry-run
```

### Resume Interrupted Jobs

```bash
# Resume all unfinished or failed downloads
loopstash resume

# Resume specific creator
loopstash resume --username natgeo
```

### View Status & History

```bash
# View overall archive statistics
loopstash status

# List recent download records
loopstash history --limit 25

# List all tracked profiles and their archive counts
loopstash profiles
```

### Verify Archive Integrity

Scan downloaded files against database records and verify SHA-256 hashes:

```bash
# Verify entire archive
loopstash verify

# Verify specific profile
loopstash verify --username natgeo
```

### Configure Settings

```bash
# View current configuration
loopstash config

# Update configuration settings
loopstash config --set max_retries=8
loopstash config --set download_dir="/mnt/hdd/LOOPSTASH"
loopstash config --set rate_limit_pause=3.0

# Reset to factory defaults
loopstash config --reset
```

---

## 📁 Archive Storage Layout

Downloads are organized in a clean, predictable hierarchy:

```text
LOOPSTASH/
└── Instagram/
    └── username/
        ├── 2026/
            ├── CwK12345XYZ.mp4
            └── CwK98765ABC.mp4
        ├── metadata/
            ├── CwK12345XYZ.json
            └── CwK98765ABC.json
        └── manifest.json
```

- Each downloaded video is accompanied by rich JSON metadata including timestamps, duration, dimensions, caption, and SHA-256 hash.
- A profile `manifest.json` tracks the collection summary and last synchronization date.

---

## 🔒 Security & Privacy

1. **Authentication Safety**: LOOPSTASH never asks for Instagram credentials. When `--browser` is used, cookies are read locally by `gallery-dl` and never sent to external servers or logged.
2. **Subprocess Isolation**: Executables are invoked using explicit argument lists without `shell=True`.
3. **No Code Execution**: Downloaded media is never automatically opened or executed.
4. **No Telemetry**: LOOPSTASH contains no metrics, phone-home code, or analytics.

---

## ⚖️ Disclaimer

LOOPSTASH is an archival utility intended for preserving content you are authorized to download. Please respect creators' rights, copyright regulations, and Instagram's Terms of Service. The maintainers do not endorse unauthorized downloading of copyrighted works.

---

## 📄 License

LOOPSTASH is licensed under the [MIT License](LICENSE).
