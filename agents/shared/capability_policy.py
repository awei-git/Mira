"""Capability classes and runtime policy defaults for Mira agents.

The current runtime routes by agent name, but production policy is defined by
the type of side effect a capability can perform. This module provides a
single place to map agents to policy classes and derive runtime behavior.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


VALID_CAPABILITY_CLASSES = {
    "read-only",
    "local-write",
    "external-draft",
    "external-publish",
    "system-mutate",
}


@dataclass(frozen=True)
class CapabilityPolicy:
    capability_class: str
    requires_preflight: bool
    requires_approval: bool
    requires_verification: bool
    fail_closed: bool
    allow_fallback_to_general: bool
    auto_retry: bool

    def to_dict(self) -> dict:
        return asdict(self)


_CLASS_POLICY_DEFAULTS: dict[str, CapabilityPolicy] = {
    "read-only": CapabilityPolicy(
        capability_class="read-only",
        requires_preflight=False,
        requires_approval=False,
        requires_verification=True,
        fail_closed=False,
        allow_fallback_to_general=True,
        auto_retry=True,
    ),
    "local-write": CapabilityPolicy(
        capability_class="local-write",
        requires_preflight=True,
        requires_approval=False,
        requires_verification=True,
        fail_closed=False,
        allow_fallback_to_general=True,
        auto_retry=True,
    ),
    "external-draft": CapabilityPolicy(
        capability_class="external-draft",
        requires_preflight=True,
        requires_approval=False,
        requires_verification=True,
        fail_closed=True,
        allow_fallback_to_general=False,
        auto_retry=True,
    ),
    "external-publish": CapabilityPolicy(
        capability_class="external-publish",
        requires_preflight=True,
        requires_approval=True,
        requires_verification=True,
        fail_closed=True,
        allow_fallback_to_general=False,
        auto_retry=False,
    ),
    "system-mutate": CapabilityPolicy(
        capability_class="system-mutate",
        requires_preflight=True,
        requires_approval=True,
        requires_verification=True,
        fail_closed=True,
        allow_fallback_to_general=False,
        auto_retry=False,
    ),
}


_DEFAULT_AGENT_CAPABILITY_CLASSES = {
    "analyst": "read-only",
    "discussion": "read-only",
    "evaluator": "read-only",
    "explorer": "read-only",
    "researcher": "read-only",
    "surfer": "read-only",
    "coder": "local-write",
    "general": "local-write",
    "health": "local-write",
    "photo": "local-write",
    "secret": "local-write",
    "video": "local-write",
    "writer": "local-write",
    "podcast": "external-draft",
    "socialmedia": "external-publish",
}


# Existing agents that already require preflight in the current runtime.
_REQUIRED_PREFLIGHT_AGENTS = {
    "general",
    "writer",
    "socialmedia",
    "podcast",
    "photo",
    "video",
    "secret",
    "health",
}


def resolve_capability_class(agent_name: str, manifest_value: str | None = None) -> str:
    """Return the canonical capability class for an agent."""
    capability_class = (manifest_value or "").strip()
    if capability_class in VALID_CAPABILITY_CLASSES:
        return capability_class
    return _DEFAULT_AGENT_CAPABILITY_CLASSES.get(agent_name, "read-only")


def get_capability_policy(agent_name: str, manifest_value: str | None = None) -> CapabilityPolicy:
    """Return the runtime policy for an agent capability.

    The policy defaults derive from capability class, then retain current
    runtime behavior where a subset of agents must fail closed on preflight.
    """
    capability_class = resolve_capability_class(agent_name, manifest_value)
    base = _CLASS_POLICY_DEFAULTS[capability_class]

    requires_preflight = (
        base.requires_preflight
        or agent_name in _REQUIRED_PREFLIGHT_AGENTS
    )
    fail_closed = base.fail_closed or agent_name in _REQUIRED_PREFLIGHT_AGENTS
    allow_fallback_to_general = (
        base.allow_fallback_to_general and not fail_closed
    )

    return CapabilityPolicy(
        capability_class=capability_class,
        requires_preflight=requires_preflight,
        requires_approval=base.requires_approval,
        requires_verification=base.requires_verification,
        fail_closed=fail_closed,
        allow_fallback_to_general=allow_fallback_to_general,
        auto_retry=base.auto_retry,
    )
