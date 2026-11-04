# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

datas = []
hiddenimports = [
    "loopstash",
    "loopstash.cli",
    "loopstash.config",
    "loopstash.database",
    "loopstash.models",
    "loopstash.logging",
    "loopstash.diagnostics",
    "loopstash.downloader",
    "loopstash.discovery",
    "loopstash.duplicate",
    "loopstash.retry",
    "loopstash.storage",
    "loopstash.storage.manager",
    "loopstash.storage.local",
    "loopstash.storage.base",
    "loopstash.backends",
    "loopstash.backends.base",
    "loopstash.backends.gallery_dl",
    "loopstash.library",
    "loopstash.library.manager",
    "loopstash.library.editor",
    "loopstash.publish",
    "loopstash.publish.oauth",
    "loopstash.publish.uploader",
    "loopstash.publish.queue",
    "loopstash.publish.scheduler",
    "loopstash.publish.quota",
    "loopstash.utils.paths",
    "loopstash.utils.validation",
    "loopstash.utils.hashing",
    "loopstash.utils.timezones",
    "googleapiclient.discovery",
    "google_auth_oauthlib.flow",
    "keyring.backends",
    "zoneinfo",
]

a = Analysis(
    ["loopstash/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="loopstash",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
