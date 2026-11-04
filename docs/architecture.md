# LOOPSTASH Architecture & System Design

This document details the architectural principles, component responsibilities, and data flows of **LOOPSTASH**.

---

## 🏛️ High-Level Architecture

LOOPSTASH is built with a strictly decoupled, modular architecture. Domain business logic is completely isolated from third-party extraction tools, database drivers, and cloud APIs.

```text
                  ┌───────────────────────────────┐
                  │         CLI Interface         │
                  │   (Typer / Rich Terminal UI)  │
                  └──────────────┬────────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         │                       │                       │
┌────────▼────────┐     ┌────────▼────────┐     ┌────────▼────────┐
│  Archive Core   │     │  Media Library  │     │ Publish/Schedule│
│ (downloader.py) │     │  (manager.py)   │     │  (uploader.py)  │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         ├───────────────────────┼───────────────────────┤
         │                       │                       │
┌────────▼────────┐     ┌────────▼────────┐     ┌────────▼────────┐
│ gallery-dl Subp │     │ Storage Adapter │     │  YouTube API v3 │
│  (backends/)    │     │  (storage/)     │     │  OAuth2 / Quota │
└─────────────────┘     └────────┬────────┘     └─────────────────┘
                                 │
                        ┌────────▼────────┐
                        │ SQLite DB (WAL) │
                        │  (database.py)  │
                        └─────────────────┘
```

---

## 🧩 Core Subsystems

### 1. Extraction Backend Abstraction (`backends/`)
- **`BaseBackend`**: Abstract base class defining `check_availability()`, `extract_reels()`, and `download_reel()`.
- **`GalleryDlBackend`**: Primary extraction adapter wrapping `gallery-dl` subprocess execution.
  - Subprocesses are executed using argument lists (never `shell=True`).
  - Output is consumed as structured JSON (`-j --simulate`).
  - Strict filters ensure only Instagram Reels (`subcategory: reels` or `/reel/` URLs) are captured.
  - Granular error classifier transforms stderr output into typed domain exceptions:
    - `ProfileNotFoundError` (HTTP 404, deleted creator)
    - `AuthenticationRequiredError` (Login challenge, private profile)
    - `RateLimitError` (HTTP 429, throttled)
    - `NetworkError` (DNS failure, socket timeout)

### 2. State & Persistence Engine (`database.py`)
- SQLite database configured with:
  - `PRAGMA journal_mode = WAL` (Write-Ahead Logging for high concurrency and crash resilience).
  - `PRAGMA synchronous = NORMAL` (Performance balance with full ACID protection).
  - `PRAGMA busy_timeout = 10000` (Avoid database locking issues across threads/processes).
- Schema migrations tracked in `schema_migrations` table.
- State Machine for Reels:
  - `PENDING` -> `DOWNLOADING` -> `COMPLETED` (or `FAILED` / `SKIPPED_DUPLICATE`).
- Automatic crash reconciliation: Interrupted `DOWNLOADING` jobs are reset to `PENDING` upon startup or when `loopstash resume` is executed.

### 3. Duplicate Detection Engine (`duplicate.py`)
- **Level 1 (Reel ID)**: Queries SQLite for existing shortcode. If marked `COMPLETED` and the media file exists with a non-zero size on disk, the download is skipped.
- **Level 2 (SHA-256 Hash)**: After downloading to a temporary buffer, the SHA-256 digest is calculated and checked against the database. If identical content exists elsewhere under a different name, the collision is resolved cleanly without overwriting.

### 4. Retry & Recovery Engine (`retry.py`)
- **Exponential Backoff**: `delay = min(max_delay, base_delay * 2^(attempt - 1))`.
- **Randomized Jitter**: Prevents synchronized thundering herds against the Instagram CDN.
- **Error Filtering**:
  - Transient network failures and HTTP 5xx are retried up to `max_retries` (default: 5).
  - Permanent errors (`ProfileNotFoundError`, `AuthenticationRequiredError`) fail fast.
  - `RateLimitError` respects the `Retry-After` header.

### 5. Storage Management (`storage/`)
- Standardized archive structure:
  - `LOOPSTASH/Instagram/<username>/<year>/<shortcode>.mp4`
  - `LOOPSTASH/Instagram/<username>/metadata/<shortcode>.json`
  - `LOOPSTASH/Instagram/<username>/manifest.json`
- Atomic Operations: Videos are downloaded to `.loopstash_tmp/<shortcode>_<username>.part` and moved into place only after validation and SHA-256 calculation.
- Cross-Platform File Sanitization (`utils/paths.py`):
  - Strips forbidden characters (`: * ? " < > | \ /`).
  - Guards against Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9`).
  - Truncates filenames exceeding filesystem limits while preserving extensions.

### 6. Persistent Media Library (`library/`)
- **Automatic Cataloging**: Downloaded Reels automatically create `media_items` records and `media_metadata` rows.
- **Query & Search**: Fast SQLite full-scan and indexing across media IDs, Instagram usernames, titles, descriptions, and hashtags.
- **Integrity Verification**: `LibraryManager.verify_library()` compares disk existence and re-computes SHA-256 digests against database baselines.
- **Metadata Editing**: `MetadataEditor` preserves original source metadata immutably while providing editable `PublishingMetadata` mapped to YouTube constraints (title <= 100 chars, description <= 5000 chars, tags <= 500 chars).
- **Import & Export**: Support for JSON and CSV export, plus importing existing folders containing MP4 videos and JSON sidecars.

### 7. YouTube Publishing Engine (`publish/`)
- **Official OAuth 2.0**: RFC-compliant `InstalledAppFlow` on local loopback, requesting narrow `youtube.upload` and `youtube.readonly` scopes.
- **Secure Token Storage**: Encrypted storage using system keyring (`secretstorage`, macOS Keychain, Windows Credential Manager) with permission-restricted fallback (`0o600` on POSIX).
- **Resumable Chunked Uploading**: Uploads stream in 4 MB chunks via `MediaFileUpload(..., resumable=True)`. Session URIs are saved to `upload_sessions` table after each chunk to recover gracefully from network drops without starting over.
- **Daily Quota Manager**: Tracks daily YouTube API units and upload counts in `quota_usage`. Preflights every upload against configured or default limits (100 daily videos) to prevent quota exhaustion lockouts.

### 8. Publication Scheduling Engine (`publish/scheduler.py` & `utils/timezones.py`)
- **Timezone Awareness**: All schedule specifications accept IANA timezone names (e.g. `America/New_York`, `Asia/Kolkata`, `UTC`) validated with Python's standard `zoneinfo.ZoneInfo`.
- **UTC ISO8601 Formatting**: Scheduled times are translated to strict UTC ISO8601 strings (`YYYY-MM-DDTHH:MM:SSZ`) required by YouTube Data API v3.
- **Future Date Guardrails**: Automatically rejects past dates or slots less than 60 seconds in the future.
- **Private Release Scheduling**: Videos are uploaded with `status.privacyStatus = "private"` and `status.publishAt = <scheduled_utc_iso>`, enabling YouTube's servers to automatically publish the video publicly at the scheduled instant.
- **Remote Rescheduling & Cancellation**: Update `publishAt` on active YouTube video resources directly via `videos().update(part="status")`.

### 9. Section 13 Safeguards (Copyright & Rights Protection)
- **Mandatory Rights Confirmation**: Both `YouTubeUploader` and `YouTubeScheduler` refuse to initiate uploads unless `rights_confirmed=True` is verified.
- **Pre-upload Hash Verification**: Before transmitting any bytes, the local file's SHA-256 digest is re-computed against the recorded database hash to ensure the asset has not been modified or corrupted on disk.

---

## 🔄 End-to-End Workflow

1. **Discovery & Download**:
   `loopstash archive instagram https://www.instagram.com/username/` discovers Reels, downloads MP4s to temporary paths, validates SHA-256, moves to organized folders, and writes JSON sidecars.
2. **Cataloging & Inspection**:
   Downloaded videos appear immediately in `loopstash library list`. Users search via `loopstash library search` and review details with `loopstash library inspect <id>`.
3. **Editing & Rights Confirmation**:
   Users prepare metadata using `loopstash library edit <id> --title "New Title" --confirm-rights`.
4. **Publishing / Scheduling**:
   - Immediate release: `loopstash publish youtube <id> --channel <channel_id> --confirm-rights`
   - Future release: `loopstash schedule create <id> --channel <channel_id> --start-date 2026-12-01 --start-time 14:00 --timezone "Asia/Kolkata"`
5. **Monitoring & Health**:
   Review daily quota usage via `loopstash publish status` or run environment diagnostics with `loopstash doctor`.
