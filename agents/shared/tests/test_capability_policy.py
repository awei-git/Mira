"""Tests for capability policy defaults and runtime classification."""
import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SHARED))
sys.path.insert(0, str(_SHARED.parent / "super"))


def test_capability_policy_defaults_for_socialmedia():
    from capability_policy import get_capability_policy

    policy = get_capability_policy("socialmedia")
    assert policy.capability_class == "external-publish"
    assert policy.requires_preflight is True
    assert policy.requires_approval is True
    assert policy.fail_closed is True
    assert policy.allow_fallback_to_general is False


def test_capability_policy_defaults_for_writer():
    from capability_policy import get_capability_policy

    policy = get_capability_policy("writer")
    assert policy.capability_class == "local-write"
    assert policy.requires_preflight is True
    assert policy.requires_verification is True


def test_registry_exposes_capability_policy():
    from agent_registry import AgentRegistry

    registry = AgentRegistry()
    assert registry.get_capability_class("writer") == "local-write"
    social_policy = registry.get_capability_policy("socialmedia")
    assert social_policy["capability_class"] == "external-publish"
    assert social_policy["requires_approval"] is True


def test_capability_policy_rejects_invalid_class():
    from capability_policy import CapabilityPolicy

    try:
        CapabilityPolicy(
            capability_class="bogus",
            requires_preflight=False,
            requires_approval=False,
            requires_verification=True,
            fail_closed=False,
            allow_fallback_to_general=True,
            auto_retry=True,
        )
    except ValueError as exc:
        assert "Invalid capability class" in str(exc)
    else:
        raise AssertionError("CapabilityPolicy should reject invalid capability classes")
