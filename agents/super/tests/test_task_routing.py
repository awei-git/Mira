"""Task routing tests — verify classification, timeout, and registry dispatch."""
from __future__ import annotations
import sys
from pathlib import Path

_AGENTS = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS / "super"))
sys.path.insert(0, str(_AGENTS / "shared"))


def test_classify_task_keywords():
    from task_manager import classify_task
    assert "writing" in classify_task("写一篇文章关于AI")
    assert "writing" in classify_task("write an essay about benchmarks")
    assert "writing" in classify_task("研究一下这个公开数据库")
    assert "podcast" in classify_task("生成podcast音频")
    assert "coding" in classify_task("fix this bug in the code")


def test_timeout_resolution():
    from task_manager import _resolve_timeout, TASK_TIMEOUT, TASK_TIMEOUT_LONG
    # Podcast = background (24h)
    assert _resolve_timeout(["podcast"]) > TASK_TIMEOUT_LONG
    # Writing = long (1h)
    assert _resolve_timeout(["writing"]) == TASK_TIMEOUT_LONG
    # General = short (15min)
    assert _resolve_timeout(["general"]) == TASK_TIMEOUT
    # Unknown tag = short
    assert _resolve_timeout(["unknown_tag"]) == TASK_TIMEOUT


def test_registry_handler_loading():
    from agent_registry import AgentRegistry
    r = AgentRegistry()
    # These core agents must load without error
    for name in ["general", "socialmedia", "analyst", "photo"]:
        handler = r.load_handler(name)
        assert callable(handler), f"{name} handler not callable"


def test_valid_agents_matches_registry():
    from agent_registry import get_registry
    registry = get_registry()
    valid = registry.get_valid_agents()
    # All registered agents should be valid
    for name in registry.list_agents():
        assert name in valid, f"{name} registered but not in valid_agents"
