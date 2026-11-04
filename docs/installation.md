# LOOPSTASH Installation Guide

**LOOPSTASH** is cross-platform and runs natively on Linux, macOS, and Windows.

---

## 📋 System Prerequisites

1. **Python 3.11+**:
   Verify your Python version:
   ```bash
   python3 --version
   ```
2. **ffmpeg** (Required for media processing):
   - **Arch Linux / Omarchy**:
     ```bash
     sudo pacman -S ffmpeg
     ```
   - **Ubuntu / Debian**:
     ```bash
     sudo apt-get update && sudo apt-get install -y ffmpeg
     ```
   - **macOS (Homebrew)**:
     ```bash
     brew install ffmpeg
     ```
   - **Windows (Chocolatey / Scoop)**:
     ```powershell
     choco install ffmpeg
     # or
     scoop install ffmpeg
     ```
3. **gallery-dl** (Instagram extraction backend):
   ```bash
   pip install gallery-dl
   # or install via package manager
   ```

---

## 📦 Installation Methods

### Method 1: Local Pip / Editable Install (Recommended for Developers)

1. Clone repository:
   ```bash
   git clone https://github.com/spdedsec/loopstash.git
   cd loopstash
   ```
2. Create virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```
3. Install package and dependencies:
   ```bash
   pip install --upgrade pip
   pip install -e .
   ```
4. Verify installation:
   ```bash
   loopstash doctor
   ```

### Method 2: User-Level Executable Install (`~/.local/bin`)

To install `loopstash` globally for your local user without activating a virtual environment every time:

```bash
mkdir -p ~/.local/bin
ln -sf /path/to/loopstash/.venv/bin/loopstash ~/.local/bin/loopstash
```
Ensure `~/.local/bin` is in your `PATH`:
```bash
export PATH="$HOME/.local/bin:$PATH"
```

### Method 3: Standalone Executable (PyInstaller)

You can build a single self-contained binary using PyInstaller:

```bash
pip install pyinstaller
pyinstaller loopstash.spec
```
The resulting executable will be in `dist/loopstash`.

---

## 🩺 System Verification

Run the built-in diagnostic suite to confirm external dependencies, database connectivity, storage directories, and credentials:

```bash
loopstash doctor
```
If all checks pass, you are ready to collect, preserve, and publish media!
