"""Tests for explicit Substack relationship target CRM."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


_SOCIALMEDIA_AGENT = Path(__file__).resolve().parents[2] / "agents" / "socialmedia"
if str(_SOCIALMEDIA_AGENT) not in sys.path:
    sys.path.insert(0, str(_SOCIALMEDIA_AGENT))


def test_seed_relationship_targets_creates_explicit_crm_records():
    from growth import DEFAULT_RELATIONSHIP_TARGETS, seed_relationship_targets

    state = {}

    created = seed_relationship_targets(state)

    assert created == len(DEFAULT_RELATIONSHIP_TARGETS)
    records = state["relationship_targets"]
    assert records["simonw"]["why_this_person"]
    assert records["simonw"]["status"] == "active"
    assert records["simonw"]["target_language"] == "en"


def test_relationship_target_subdomains_prioritizes_active_allowed_targets():
    from growth import _relationship_target_subdomains, seed_relationship_targets

    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    state = {}
    seed_relationship_targets(state)
    state["relationship_targets"]["simonw"]["next_allowed_at"] = future
    state["relationship_targets"]["latentspace"]["do_not_comment_reason"] = "No useful angle this week."

    subdomains = _relationship_target_subdomains(state)

    assert "simonw" not in subdomains
    assert "latentspace" not in subdomains
    assert subdomains[0] in {"nathanlambert", "interconnects", "boundaryintelligence"}
