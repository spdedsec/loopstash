"""SHA-256 file hashing and verification utilities."""

from __future__ import annotations

import hashlib
from pathlib import Path


def compute_file_sha256(path: Path, buffer_size: int = 65536) -> str:
    """Compute the SHA-256 hex digest of a file in streaming chunks."""
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(buffer_size):
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def verify_file_hash(path: Path, expected_hash: str) -> bool:
    """Verify if the file exists and matches the expected SHA-256 hash."""
    if not path.is_file():
        return False
    actual_hash = compute_file_sha256(path)
    return actual_hash.lower() == expected_hash.strip().lower()
