"""Regression coverage for scheduled social growth imports."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


_SOCIALMEDIA_AGENT = Path(__file__).resolve().parents[2] / "agents" / "socialmedia"
if str(_SOCIALMEDIA_AGENT) not in sys.path:
    sys.path.insert(0, str(_SOCIALMEDIA_AGENT))


def test_growth_imports_trust_positioning_guard_from_mira_package():
    from mira import _content_has_trust_positioning_claim

    assert _content_has_trust_positioning_claim("You can trust Mira with this.") is True
    assert _content_has_trust_positioning_claim("This comment asks a concrete follow-up question.") is False

    growth = importlib.import_module("growth")

    assert growth._content_has_trust_positioning_claim is _content_has_trust_positioning_claim
