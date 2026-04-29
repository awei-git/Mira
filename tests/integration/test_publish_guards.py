"""CLAUDE.md Rule 3 — three publish guardrails still present.

The refactor split substack.py into substack_publish.py; this test
locks that the three hard-rule guard calls are still wired so future
edits can't silently drop them.
"""

from __future__ import annotations

import re
from pathlib import Path


_AGENT_ROOT = Path(__file__).resolve().parent.parent.parent / "agents"
_PUBLISH_FILE = _AGENT_ROOT / "socialmedia" / "substack_publish.py"


def test_publish_file_exists():
    assert _PUBLISH_FILE.exists(), "substack_publish.py must remain"


def test_publish_invokes_content_error_check():
    src = _PUBLISH_FILE.read_text(encoding="utf-8")
    # The content-guard name is defined in substack.py; publish imports+uses it.
    assert re.search(
        r"(?:_content_has_unverified_security_claims|_content_looks_like_error)",
        src,
    ), "content guard helper must be referenced"


def test_publish_invokes_preflight_check():
    src = _PUBLISH_FILE.read_text(encoding="utf-8")
    assert "preflight_check" in src, "preflight_check must be called before publish"


def test_publish_enforces_cooldown():
    src = _PUBLISH_FILE.read_text(encoding="utf-8")
    assert re.search(
        r"PUBLISH_COOLDOWN|days_since|cooldown", src, re.IGNORECASE
    ), "publish cooldown logic must be present in substack_publish.py"


def test_publish_blocked_under_pytest():
    """Safety rail: production publish must refuse when running in a test harness."""
    src = _PUBLISH_FILE.read_text(encoding="utf-8")
    assert "PYTEST_CURRENT_TEST" in src, "pytest guard must remain"
