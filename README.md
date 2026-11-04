# LOOPSTASH

> **Collect. Preserve. Organise.**  
> *A cross-platform media archiving, management, and publishing application.*

[![CI](https://github.com/spdedsec/loopstash/actions/workflows/ci.yml/badge.svg)](https://github.com/spdedsec/loopstash/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

---

## ⚡ Overview

**LOOPSTASH** is a lightweight, privacy-conscious media archiving, management, and publishing application. It provides an end-to-end workflow to:
1. **Collect**: Discover and download accessible Instagram Reels in highest source quality.
2. **Preserve**: Capture full original metadata in JSON sidecars alongside untouched MP4 files.
3. **Organise**: Manage a persistent local media library with inspection, search, verification, and editing.
4. **Publish & Schedule**: Connect your YouTube channel via official OAuth 2.0, publish immediately, or schedule future public releases via YouTube's `status.publishAt`.

---

## ✨ Features

- **Strict Scope Archiving**: Captures Instagram Reels exclusively; filters photos, carousels, stories, and highlights.
- **Dual-Layer Duplicate Detection**: Skips already archived items via database shortcodes and SHA-256 content hashes.
- **Crash Recovery & Resume**: Resumes interrupted downloads and uploads without re-downloading or re-uploading completed chunks.
- **Persistent Media Library**: Auto-registers downloaded items, provides full-text search, integrity verification, and metadata export/import.
- **Metadata Separation**: Preserves original source metadata immutably while offering editable publishing titles, descriptions, and tags mapped to YouTube constraints.
- **Official YouTube OAuth 2.0**: RFC-compliant token acquisition with secure OS keyring storage and permission-restricted file fallback (`0o600`).
- **Resumable 4MB Chunked Uploads**: Native Google resumable upload protocol with persistent session URIs in SQLite.
- **Publication Calendar & Scheduling**: Timezone-aware slot generation using standard IANA timezones and YouTube's native `status.publishAt`.
- **Daily Quota Manager**: Tracks API usage and enforces daily upload limits to prevent account lockouts.
- **Section 13 Copyright Safeguards**: Mandatory rights confirmation and pre-upload SHA-256 integrity verification.
- **Cross-Platform**: Runs natively on Arch Linux, Ubuntu, macOS, and Windows.

---

## 🚀 Quick Start

### Installation

Requires **Python 3.11+**, `gallery-dl`, and `ffmpeg`.

```bash
git clone https://github.com/spdedsec/loopstash.git ~/loopstash
cd ~/loopstash

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Verify your installation:
```bash
loopstash doctor
```

Install executable symlink in user PATH:
```bash
mkdir -p ~/.local/bin
ln -sf ~/loopstash/.venv/bin/loopstash ~/.local/bin/loopstash
```

---

## 💻 CLI Usage Guide

### 1. Archive Instagram Reels

```bash
# Archive public profile Reels
loopstash archive instagram https://www.instagram.com/creator/

# Limit to 10 most recent Reels
loopstash archive instagram creator --max-items 10

# Resume interrupted downloads
loopstash archive resume

# View archived profiles and counts
loopstash archive profiles
```

### 2. Media Library Management

```bash
# List all cataloged videos
loopstash library list

# Search by creator, title, or tags
loopstash library search "tech demonstration"

# Inspect detailed metadata and disk integrity
loopstash library inspect <MEDIA_ID>

# Edit publishing metadata and confirm rights
loopstash library edit <MEDIA_ID> \
  --title "Clean Video Title" \
  --description "Description text #demo" \
  --confirm-rights

# Verify disk files and checksums
loopstash library verify

# Export library catalog
loopstash library export --format json --output library.json
```

### 3. YouTube Publishing

```bash
# Authenticate YouTube channel via OAuth 2.0
loopstash publish auth youtube --client-secrets /path/to/client_secret.json

# View connected channels
loopstash publish accounts

# Preview metadata and check quota before uploading
loopstash publish preview <MEDIA_ID>

# Publish video immediately (requires copyright rights confirmation)
loopstash publish youtube <MEDIA_ID> --channel <CHANNEL_ID> --confirm-rights

# Check daily quota usage
loopstash publish status
```

### 4. Publication Scheduling

```bash
# Preview publication calendar slots
loopstash schedule preview \
  --start-date 2026-12-01 \
  --start-time 14:00 \
  --timezone "Asia/Kolkata" \
  --count 7 \
  --interval daily

# Schedule batch release with status.publishAt
loopstash schedule create <MEDIA_ID_1> <MEDIA_ID_2> \
  --channel <CHANNEL_ID> \
  --start-date 2026-12-01 \
  --start-time 14:00 \
  --interval every_2d \
  --timezone "Asia/Kolkata" \
  --confirm-rights

# List active scheduled entries
loopstash schedule list

# Reschedule or cancel
loopstash schedule reschedule <JOB_ID> --date 2026-12-05 --time 16:00 --timezone "Asia/Kolkata"
loopstash schedule cancel <JOB_ID>
```

---

## 📚 Documentation

- [Architecture & Design](docs/architecture.md)
- [Installation Guide](docs/installation.md)
- [Instagram Archiving](docs/instagram.md)
- [YouTube Publishing](docs/youtube.md)
- [Scheduling Engine](docs/scheduling.md)
- [Troubleshooting](docs/troubleshooting.md)

---

## 📄 License

LOOPSTASH is open-source software licensed under the [MIT License](LICENSE).
