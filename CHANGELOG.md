# Changelog

All notable changes to **LOOPSTASH** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-11-04

### Added
- **Persistent Media Library (`loopstash library`)**:
  - Auto-registration of archived Instagram Reels into the library catalog.
  - Multi-attribute search across creators, titles, captions, shortcodes, and hashtags.
  - Disk integrity checker verifying media files and re-computing SHA-256 digests.
  - Metadata editor validating YouTube field constraints while preserving immutable original source metadata.
  - Catalog export to JSON and CSV formats.
  - Directory import with automatic JSON sidecar discovery and duplicate detection.
- **YouTube Publishing Engine (`loopstash publish`)**:
  - Official Google OAuth 2.0 flow with minimal requested scopes (`youtube.upload`, `youtube.readonly`).
  - Dual credential security: system keyring support with restricted fallback (`0o600` file permissions).
  - Resumable 4MB chunked file uploads surviving network interruptions.
  - Section 13 Copyright Safeguards requiring explicit user rights confirmation.
  - Pre-upload SHA-256 digest validation against cataloged database records.
  - Daily YouTube API quota tracking and rate limit protection in SQLite.
- **Publication Scheduling Engine (`loopstash schedule`)**:
  - Publication calendar slot preview and batch scheduling.
  - Standard IANA timezone parsing (`zoneinfo.ZoneInfo`) and UTC ISO8601 formatting.
  - Private video upload paired with YouTube's native `status.publishAt` parameter.
  - Remote YouTube rescheduling and local schedule cancellation.
- **Enhanced CLI & Diagnostics**:
  - Typer CLI app restructured with `archive`, `library`, `publish`, `schedule`, `doctor`, and `config`.
  - Comprehensive `doctor` diagnostics suite verifying dependencies, database WAL mode, and YouTube accounts.
  - Full test suite expanded to 98 automated tests across all modules.
  - Multi-OS GitHub Actions CI matrix and PyInstaller build spec (`loopstash.spec`).

---

## [0.1.0] - 2026-11-04

### Added
- **Reels-Only Archiving**: Bulk download accessible Instagram Reels from public profiles in original quality without re-encoding.
- **Pluggable Backend Interface**: Modular `BaseBackend` abstraction with `GalleryDlBackend` adapter.
- **Dual-Layer Duplicate Detection**:
  - Reel-level detection tracking shortcodes and media IDs in SQLite.
  - File-level detection computing SHA-256 hashes to prevent redundant downloads and manage collisions safely.
- **Crash Recovery & Resume**:
  - SQLite persistent state with automatic recovery of interrupted `DOWNLOADING` jobs.
  - `loopstash resume` command to resume unfinished downloads.
- **Smart Bounded Retries**:
  - Exponential backoff with random jitter.
  - Granular error classification distinguishing transient network failures from permanent auth/private errors.
  - Respects HTTP 429 `Retry-After` header.
- **Storage Management**:
  - Predictable hierarchical folder structure (`LOOPSTASH/Instagram/<username>/<year>/<shortcode>.mp4`).
  - Sidecar metadata files (`<shortcode>.json`) and overall profile `manifest.json`.
  - Cross-platform path sanitization supporting Unicode names, spaces in paths, and Windows reserved filenames.
  - Pre-flight storage space checks.
- **Terminal CLI UX**:
  - Rich-powered progress reporting and summary tables.
  - Commands: `download`, `resume`, `status`, `history`, `profiles`, `verify`, `doctor`, `config`.
