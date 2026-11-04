# Changelog

All notable changes to **LOOPSTASH** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
  - Beautiful Rich-powered progress reporting and summary tables.
  - Commands: `download`, `resume`, `status`, `history`, `profiles`, `verify`, `doctor`, `config`.
  - Flags: `--max-retries`, `--force`, `--dry-run`, `--output`, `--browser`, `--verbose`, `--no-color`.
- **Comprehensive Test Suite**:
  - Automated tests covering URL validation, SQLite persistence, duplicate detection, retry logic, storage handling, and resume orchestration.
  - Ruff linting and formatting configuration.
- **Documentation & CI**:
  - Complete `README.md`, `INSTALL.md`, `CONTRIBUTING.md`, `docs/architecture.md`, `docs/troubleshooting.md`.
  - Multi-OS GitHub Actions CI workflow (Linux, macOS, Windows).
