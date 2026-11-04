# LOOPSTASH Installation Guide

This guide provides instructions for installing and running **LOOPSTASH** across supported operating systems.

---

## 📋 System Requirements

- **Python**: Version 3.11 or newer
- **Extraction Backend**: `gallery-dl` (version 1.28.0+)
- **Media Processor**: `ffmpeg` (recommended for stream packaging)
- **RAM**: Minimum 512 MB available
- **Storage**: Minimum 500 MB free space (or external HDD)

---

## 🐧 1. Arch Linux / Omarchy

```bash
# 1. Install system dependencies
sudo pacman -S python python-pip gallery-dl ffmpeg git

# 2. Clone the repository
git clone https://github.com/spdedsec/loopstash.git ~/loopstash
cd ~/loopstash

# 3. Create isolated virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 4. Install LOOPSTASH in editable mode with development dependencies
pip install --upgrade pip
pip install -e ".[dev]"

# 5. Enable global CLI access
mkdir -p ~/.local/bin
ln -sf ~/loopstash/.venv/bin/loopstash ~/.local/bin/loopstash

# 6. Verify installation
loopstash doctor
```

---

## 🐧 2. Ubuntu / Debian

```bash
# 1. Install prerequisites
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg git

# 2. Clone repository
git clone https://github.com/spdedsec/loopstash.git ~/loopstash
cd ~/loopstash

# 3. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 4. Install gallery-dl and LOOPSTASH
pip install --upgrade pip
pip install gallery-dl
pip install -e ".[dev]"

# 5. Symlink executable
mkdir -p ~/.local/bin
ln -sf ~/loopstash/.venv/bin/loopstash ~/.local/bin/loopstash

# Ensure ~/.local/bin is in PATH
export PATH="$HOME/.local/bin:$PATH"

# 6. Verify
loopstash doctor
```

---

## 🍏 3. macOS

```bash
# 1. Install prerequisites via Homebrew
brew install python gallery-dl ffmpeg git

# 2. Clone repository
git clone https://github.com/spdedsec/loopstash.git ~/loopstash
cd ~/loopstash

# 3. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 4. Install LOOPSTASH
pip install --upgrade pip
pip install -e ".[dev]"

# 5. Symlink to /usr/local/bin or ~/.local/bin
mkdir -p ~/.local/bin
ln -sf "$PWD/.venv/bin/loopstash" ~/.local/bin/loopstash

# 6. Run diagnostics
loopstash doctor
```

---

## 🪟 4. Windows 10 / 11

### Prerequisites via Windows Package Manager (`winget`):

```powershell
# Open PowerShell as Administrator
winget install Python.Python.3.11
winget install Gyan.FFmpeg
winget install mikf.gallery-dl
```

### Setup:

```powershell
# Clone repository
git clone https://github.com/spdedsec/loopstash.git loopstash
cd loopstash

# Create virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# Install package
pip install -e .[dev]

# Test CLI
loopstash doctor
```

---

## 📦 5. Building Standalone Executable (PyInstaller)

To build a standalone executable that does not require Python on the target machine:

```bash
# Activate virtual environment
source .venv/bin/activate

# Install PyInstaller
pip install pyinstaller

# Build single-file binary
pyinstaller \
    --name loopstash \
    --onefile \
    --clean \
    --add-data "loopstash:loopstash" \
    loopstash/__main__.py

# Binary output will be located in dist/loopstash
./dist/loopstash --help
```

---

## 🩺 Verifying Health

Always run the built-in diagnostic suite to confirm proper installation:

```bash
loopstash doctor
```

Output should show all checks passing:
```text
  Check                 Status   Details
 ────────────────────────────────────────────────────────────────────────
  Python Version        PASS     Python 3.14.x (...
  Installation          PASS     Editable package (...
  Backend (gallery-dl)  PASS     gallery-dl 1.28.x (...
  Media Engine (ffmpeg) PASS     ffmpeg 8.x.x
  Configuration         PASS     Valid config loaded (...
  Database Health       PASS     SQLite integrity check passed
  Storage & Disk        PASS     Write OK (...
```
