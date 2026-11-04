# Instagram Reels Archiving Guide

**LOOPSTASH** provides automated discovery, preservation, and duplicate detection for accessible Instagram Reels.

---

## 🎯 Scope & Boundaries

- **Reels Only**: LOOPSTASH targets Instagram Reels exclusively. It strictly filters out photos, regular multi-image carousels, stories, highlights, profile avatars, and tagged posts.
- **Source Quality**: Always downloads the highest available resolution stream provided by the source CDN without re-encoding or compression.
- **Privacy Conscious**: Works anonymously out-of-the-box. Never asks for or stores Instagram passwords.

---

## 🚀 Archiving Commands

### Basic Profile Archiving

Provide either an Instagram username (with or without `@`) or a full profile URL:

```bash
# By username
loopstash archive instagram natgeo

# By URL
loopstash archive instagram https://www.instagram.com/natgeo/
```

### Options & Flags

- `--output / -o <PATH>`: Override the default download directory.
- `--browser / -b <BROWSER>`: Extract session cookies from a local browser (`firefox`, `chrome`, `chromium`, `brave`, `edge`) to access age-restricted or rate-limited public content.
- `--max-items / -n <INT>`: Bounded extraction (e.g. download only the 10 most recent Reels).
- `--max-retries <INT>`: Maximum retries per Reel before flagging failure (default: 5).
- `--dry-run`: Simulate discovery and display matching Reels without writing video files to disk.
- `--force / -f`: Re-download Reels even if marked completed in the database.
- `--verbose / -v`: Show detailed debug logs and gallery-dl command execution.

---

## 🔄 Resuming Interrupted Operations

If a network disconnection or machine restart interrupts a download session, resume seamlessly with:

```bash
# Resume incomplete reels across all profiles
loopstash archive resume

# Resume specific profile
loopstash archive resume natgeo
```

LOOPSTASH checks existing database state and disk files to avoid redownloading intact media.

---

## 📂 Preserved Metadata Sidecars

For every downloaded Reel, a JSON sidecar is written alongside the MP4 file:

```text
LOOPSTASH/Instagram/creator_name/2026/
├── CwK12345XYZ.mp4
└── CwK12345XYZ.json
```

The sidecar preserves:
- Original creator username and profile URL
- Reel shortcode and unique ID
- Full original caption with emojis and language preserved
- Upload timestamp (UTC) and download timestamp
- Extracted hashtags
- Duration, width, height, and file size
- Raw backend response from the extraction engine
