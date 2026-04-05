"""Smoke tests — verify core modules import and basic functions work."""
from __future__ import annotations
import sys
from pathlib import Path

_AGENTS = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS / "super"))
sys.path.insert(0, str(_AGENTS / "shared"))
sys.path.insert(0, str(_AGENTS / "writer"))


def test_core_imports():
    import core
    assert hasattr(core, "cmd_run"), "core.py missing cmd_run"


def test_config_imports():
    from config import MIRA_ROOT, STATE_FILE
    # In CI, MIRA_ROOT may not exist — just check the import works
    assert MIRA_ROOT is not None


def test_registry_loads():
    from agent_registry import AgentRegistry
    r = AgentRegistry()
    agents = r.list_agents()
    assert len(agents) >= 12, f"Expected 12+ agents, got {len(agents)}"
    assert "writer" in agents
    assert "general" in agents
    assert "podcast" in agents


def test_soul_loads():
    from soul_manager import load_soul
    soul = load_soul()
    assert isinstance(soul, dict), f"load_soul returned {type(soul)}"
    assert "identity" in soul, "Soul missing identity"
    assert "worldview" in soul, "Soul missing worldview"
