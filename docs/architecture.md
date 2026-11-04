# LOOPSTASH Architecture & System Design

This document details the architectural principles, component responsibilities, and data flows of **LOOPSTASH**.

---

## 🏛️ High-Level Architecture

LOOPSTASH is built with a strictly decoupled, modular architecture. Domain business logic is completely isolated from third-party extraction tools and database drivers.

```text
                  ┌───────────────────────────────┐
                  │         CLI Interface         │
                  │   (Typer / Rich Terminal UI)  │
                  └──────────────┬────────────────┘
                                 │
                  ┌──────────────▼────────────────┐
                  │      Download Coordinator     │
                  │         (downloader.py)       │
                  └──────┬──────────────┬─────────┘
                         │              │
        ┌────────────────┼──────────────┼───────────────┐
        │                │              │               │
┌───────▼────────┐┌──────▼──────┐┌──────▼───────┐┌──────▼────────┐
│   Discovery    ││  Duplicate  ││    Retry     ││    Storage    │
│  & Validation  ││  Detection  ││    Engine    ││   Management  │
│ (discovery.py) ││(duplicate.py)││  (retry.py)  ││  (storage.py) │
└───────┬────────┘└──────┬──────┘└──────┬───────┘└──────┬────────┘
        │                │              │               │
        └────────────────┼──────────────┼───────────────┘
                         │              │
                  ┌──────▼──────┐┌──────▼───────┐
                  │  Extraction ││  SQLite DB   │
                  │   Backend   ││ State Layer  │
                  │ (gallery-dl)││(database.py) │
                  └─────────────┘└──────────────┘
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

### 5. Storage Management (`storage.py`)
- Standardized archive structure:
  - `LOOPSTASH/Instagram/<username>/<year>/<shortcode>.mp4`
  - `LOOPSTASH/Instagram/<username>/metadata/<shortcode>.json`
  - `LOOPSTASH/Instagram/<username>/manifest.json`
- Atomic Operations: Videos are downloaded to `.loopstash_tmp/<shortcode>_<username>.part` and moved into place only after validation and SHA-256 calculation.
- Cross-Platform File Sanitization (`utils/paths.py`):
  - Strips forbidden characters (`: * ? " < > | \ /`).
  - Guards against Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9`).
  - Truncates filenames exceeding filesystem limits while preserving extensions.

---

## 🔄 End-to-End Download Workflow

1. **Input Validation**: The user inputs `@username` or a profile URL. `validate_instagram_url()` standardizes the username and URL format.
2. **Pre-flight Storage Check**: `check_disk_space()` ensures target directory has sufficient free MB (default: 500 MB).
3. **Reel Discovery**: `backend.extract_reels()` simulates extraction to retrieve metadata for all accessible Reels.
4. **Duplicate Filtering**: `duplicate_detector.check_reel_duplicate()` compares discovered shortcodes with SQLite.
5. **Download Pipeline**: For each pending Reel:
   - Mark state `DOWNLOADING` in SQLite.
   - Execute download to staging directory with bounded retries.
   - Compute SHA-256 and verify file is non-empty.
   - Atomically move file to year-partitioned target folder.
   - Write sidecar JSON metadata.
   - Mark state `COMPLETED` in SQLite with file size and hash.
6. **Manifest Finalization**: Update `manifest.json` with total archived count, size in bytes, and timestamp.
