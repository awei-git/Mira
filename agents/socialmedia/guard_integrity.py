"""Integrity verification for Substack publish guard code."""

import hashlib
from pathlib import Path


GUARD_MODULE_PATH = Path(__file__).resolve().with_name("content_guard.py")
EXPECTED_SHA256 = "2403f123106fcf0b14dcef750e43c6a32d555e7f6375bb44f682a52270cc172b"  # pragma: allowlist secret


def verify() -> bool:
    try:
        current_sha256 = hashlib.sha256(GUARD_MODULE_PATH.read_bytes()).hexdigest()
    except OSError:
        return False
    return current_sha256 == EXPECTED_SHA256
