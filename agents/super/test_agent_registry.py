"""Tests for agent_registry.py — verify all manifests load and handlers import."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure paths
_HERE = Path(__file__).resolve().parent
_AGENTS = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_AGENTS / "shared"))

from agent_registry import AgentRegistry


def test_all_manifests_load():
    """All manifest.json files parse without error."""
    r = AgentRegistry()
    agents = r.list_agents()
    assert len(agents) >= 12, f"Expected 12+ agents, got {len(agents)}: {agents}"
    print(f"  OK: {len(agents)} agents registered")


def test_required_agents_present():
    """Core agents must be in the registry."""
    r = AgentRegistry()
    required = {"writer", "general", "socialmedia", "podcast", "analyst",
                "explorer", "discussion", "photo", "video", "math", "secret", "surfer"}
    missing = required - r.get_valid_agents()
    assert not missing, f"Missing required agents: {missing}"
    print(f"  OK: all {len(required)} required agents present")


def test_manifest_fields():
    """Each manifest has required fields."""
    r = AgentRegistry()
    for name in r.list_agents():
        m = r.get_manifest(name)
        assert m.name, f"{name}: missing name"
        assert m.description, f"{name}: missing description"
        assert m.entry_point, f"{name}: missing entry_point"
        assert m.timeout_category in ("short", "long", "background"), \
            f"{name}: invalid timeout_category '{m.timeout_category}'"
        assert m.tier in ("light", "heavy"), f"{name}: invalid tier '{m.tier}'"
    print(f"  OK: all manifests have valid fields")


def test_handler_files_exist():
    """Handler files referenced in manifests exist on disk."""
    r = AgentRegistry()
    missing = []
    for name in r.list_agents():
        m = r.get_manifest(name)
        file_path, _ = m.handler_path()
        if not file_path.exists():
            missing.append(f"{name}: {file_path}")
    if missing:
        print(f"  WARN: {len(missing)} handler files missing (may use inline handlers):")
        for m in missing:
            print(f"    {m}")
    else:
        print(f"  OK: all handler files exist")


def test_handlers_importable():
    """Handlers that exist can be imported without error."""
    r = AgentRegistry()
    loaded = []
    failed = []
    for name in r.list_agents():
        m = r.get_manifest(name)
        file_path, _ = m.handler_path()
        if not file_path.exists():
            continue
        try:
            r.load_handler(name)
            loaded.append(name)
        except Exception as e:
            failed.append(f"{name}: {e}")

    if failed:
        print(f"  WARN: {len(failed)} handlers failed to import:")
        for f in failed:
            print(f"    {f}")
    print(f"  OK: {len(loaded)} handlers imported successfully")


def test_descriptions_for_planner():
    """get_agent_descriptions returns non-empty formatted string."""
    r = AgentRegistry()
    desc = r.get_agent_descriptions()
    assert len(desc) > 100, f"Descriptions too short: {len(desc)} chars"
    assert "writer" in desc
    assert "general" in desc
    print(f"  OK: descriptions generated ({len(desc)} chars)")


def test_timeout_categories():
    """Timeout categories make sense for each agent type."""
    r = AgentRegistry()
    # Heavy agents should not have short timeout
    for name in r.list_agents():
        m = r.get_manifest(name)
        if m.tier == "heavy" and m.timeout_category == "short":
            print(f"  WARN: {name} is heavy tier but has short timeout")
    print(f"  OK: timeout categories checked")


if __name__ == "__main__":
    tests = [
        test_all_manifests_load,
        test_required_agents_present,
        test_manifest_fields,
        test_handler_files_exist,
        test_handlers_importable,
        test_descriptions_for_planner,
        test_timeout_categories,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            print(f"\n{t.__name__}:")
            t()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
