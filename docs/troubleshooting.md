# LOOPSTASH Troubleshooting Guide

This guide covers common issues, Instagram extraction hurdles, and recovery procedures.

---

## 🔍 Diagnostic First Step

Before debugging manually, run the built-in diagnostic suite:

```bash
loopstash doctor
```

`loopstash doctor` inspects:
- Python version compatibility
- Package installation status
- `gallery-dl` and `ffmpeg` binary presence and version
- Database integrity and schema version
- Storage destination write permissions and available free disk space

---

## 🛑 Common Issues and Resolutions

### 1. `BackendNotFoundError: gallery-dl binary not found`

**Cause:** The extraction engine `gallery-dl` is not installed or not in your system `$PATH`.

**Solution:**
- **Arch Linux / Omarchy:**
  ```bash
  sudo pacman -S gallery-dl
  ```
- **Ubuntu / Debian:**
  ```bash
  pip install gallery-dl
  ```
- **macOS:**
  ```bash
  brew install gallery-dl
  ```
- **Windows:**
  ```powershell
  winget install mikf.gallery-dl
  ```

---

### 2. `AuthenticationRequiredError: Instagram requires authentication or account is private`

**Cause:** Instagram periodically presents login challenges, checkpoint prompts, or the requested creator has set their profile to private.

**Solution:**
Use browser session cookies from a browser where you are logged into Instagram:

```bash
loopstash download @username --browser firefox
# Or Chrome / Chromium
loopstash download @username --browser chrome
```

> **Note on Security:** LOOPSTASH does not store, transmit, or log cookie data. Session cookies are accessed locally by `gallery-dl` to authenticate against Instagram endpoints directly.

---

### 3. `RateLimitError: Instagram rate limit reached (HTTP 429)`

**Cause:** Instagram detected high-frequency requests from your IP address and temporarily throttled access.

**Solution:**
1. Wait 15–30 minutes for the rate limit window to expire.
2. Increase the inter-download delay in configuration:
   ```bash
   loopstash config --set rate_limit_pause=4.0
   ```
3. Resume the download job:
   ```bash
   loopstash resume --username <username>
   ```

---

### 4. `Insufficient disk space at destination`

**Cause:** Available disk space at the configured download path is below `min_free_space_mb` (default: 500 MB).

**Solution:**
- Free up disk space on the target volume.
- Or point LOOPSTASH to an external drive:
   ```bash
   loopstash config --set download_dir="/mnt/external_drive/LOOPSTASH"
   ```
- Alternatively, adjust the threshold if space is intentionally constrained:
   ```bash
   loopstash config --set min_free_space_mb=200
   ```

---

### 5. Interrupted Downloads After Crash or Server Restart

**Cause:** System restart, power interruption, or network disconnect while downloading.

**Solution:**
LOOPSTASH records progress in SQLite. Stale `DOWNLOADING` jobs are automatically recovered. Simply run:

```bash
loopstash resume
```

Any completed Reels will be skipped, and remaining or interrupted Reels will continue automatically.

---

### 6. Verifying and Repairing Corrupted Files

If files were modified, moved, or truncated outside of LOOPSTASH:

```bash
loopstash verify
```

If a Reel is marked `COMPLETED` in the database but the file has been deleted from disk, LOOPSTASH will automatically detect the missing file on the next download run and re-download it.

To force redownloading all Reels regardless of state:

```bash
loopstash download @username --force
```
