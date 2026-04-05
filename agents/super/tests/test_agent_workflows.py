"""Integration tests — verify each agent can be triggered and produces output.

These tests use REAL LLM calls (costs tokens). Run sparingly, not in CI loops.
Each test creates a temp workspace, calls the handler, and checks for non-empty output.

Usage:
    pytest test_agent_workflows.py -v              # all agents
    pytest test_agent_workflows.py -k general -v   # single agent
    pytest test_agent_workflows.py -v --timeout 120  # with timeout
"""
from __future__ import annotations
import json
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

_AGENTS = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS / "super"))
sys.path.insert(0, str(_AGENTS / "shared"))
sys.path.insert(0, str(_AGENTS / "writer"))
sys.path.insert(0, str(_AGENTS / "explorer"))

from agent_registry import AgentRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_workspace() -> Path:
    """Create a temp workspace directory that looks like a real task workspace."""
    ws = Path(tempfile.mkdtemp(prefix="mira_test_"))
    return ws


def _task_id() -> str:
    return f"test_{uuid.uuid4().hex[:8]}"


def _call_agent(agent_name: str, content: str, tier: str = "light") -> str:
    """Load agent from registry and call it. Returns output text.

    Handles handler signature differences — some accept 'tier', some don't.
    Some use 'content', others 'instruction'.
    """
    import inspect
    registry = AgentRegistry()
    handler = registry.load_handler(agent_name)
    workspace = _make_workspace()
    task_id = _task_id()

    # Inspect handler signature to pass only accepted params
    sig = inspect.signature(handler)
    params = sig.parameters

    kwargs = {
        "workspace": workspace,
        "task_id": task_id,
        "sender": "ang",
        "thread_id": "",
    }
    # Some handlers use 'content', others 'instruction'
    if "instruction" in params:
        kwargs["instruction"] = content
    else:
        kwargs["content"] = content

    if "tier" in params:
        kwargs["tier"] = tier

    result = handler(**kwargs)
    return result or ""


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

def test_registry_discovers_all_agents():
    """Registry should find all agent manifests."""
    registry = AgentRegistry()
    agents = registry.list_agents()
    # At minimum these core agents should exist
    expected = {"general", "writer", "analyst", "coder", "researcher",
                "discussion", "socialmedia", "podcast", "surfer", "secret"}
    missing = expected - set(agents)
    assert not missing, f"Missing agents: {missing}. Found: {agents}"


# Agents with known missing/broken handlers (TODO: fix these)
_KNOWN_BROKEN = set()  # All agents should load


def test_registry_loads_all_handlers():
    """Every registered agent should have a loadable handler."""
    registry = AgentRegistry()
    failures = []
    for name in registry.list_agents():
        if name in _KNOWN_BROKEN:
            continue
        try:
            handler = registry.load_handler(name)
            assert callable(handler), f"{name}: handler not callable"
        except (ImportError, KeyError) as e:
            failures.append(f"{name}: {e}")
    assert not failures, f"Failed to load handlers:\n" + "\n".join(failures)


def test_known_broken_agents_are_documented():
    """Ensure we know which agents are broken and they're tracked."""
    registry = AgentRegistry()
    actually_broken = set()
    for name in registry.list_agents():
        try:
            registry.load_handler(name)
        except (ImportError, KeyError):
            actually_broken.add(name)
    undocumented = actually_broken - _KNOWN_BROKEN
    assert not undocumented, f"Newly broken agents not in _KNOWN_BROKEN: {undocumented}"


# ---------------------------------------------------------------------------
# Per-agent workflow tests (real LLM calls)
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_general_agent():
    """General agent should answer a simple question."""
    result = _call_agent("general", "What is 2+2? Reply with just the number.")
    assert result, "General agent returned empty"
    assert "4" in result, f"Expected '4' in response, got: {result[:200]}"


@pytest.mark.slow
def test_discussion_agent():
    """Discussion agent should engage in conversation."""
    result = _call_agent("discussion", "你觉得今天天气怎么样？简短回复。")
    assert result, "Discussion agent returned empty"
    assert len(result) > 10, f"Discussion response too short: {result}"


@pytest.mark.slow
def test_researcher_agent():
    """Researcher agent should solve a math/research problem."""
    result = _call_agent("researcher", "Prove that sqrt(2) is irrational. Keep it brief.", tier="heavy")
    assert result, "Math agent returned empty"
    assert len(result) > 50, f"Math response too short: {result[:100]}"


@pytest.mark.slow
def test_analyst_agent():
    """Analyst agent should produce market analysis."""
    result = _call_agent("analyst", "简要分析一下NVIDIA当前的竞争优势，100字以内。")
    assert result, "Analyst agent returned empty"
    assert len(result) > 30, f"Analyst response too short: {result[:100]}"


@pytest.mark.slow
def test_coder_agent():
    """Coder agent should complete a coding task."""
    result = _call_agent("coder", "Write a Python function that checks if a string is a palindrome. Just the code, no explanation.")
    assert result, "Coder agent returned empty"
    # Coder may return code directly or a summary (code written to output.md)
    assert len(result) > 20, f"Coder response too short: {result}"


@pytest.mark.slow
def test_secret_agent():
    """Secret agent should handle private requests (local model)."""
    result = _call_agent("secret", "What day of the week is March 27, 2026? Reply briefly.")
    assert result, "Secret agent returned empty"


@pytest.mark.slow
def test_surfer_agent():
    """Surfer agent should handle web search requests."""
    result = _call_agent("surfer", "Search for the current weather in Tokyo. Summarize briefly.")
    assert result, "Surfer agent returned empty"


@pytest.mark.slow
def test_socialmedia_agent():
    """Socialmedia agent should generate content (NOT publish — just draft)."""
    result = _call_agent("socialmedia",
                         "Draft a Substack note about learning to code. DO NOT publish, just return the draft text.")
    assert result, "Socialmedia agent returned empty"


# ---------------------------------------------------------------------------
# Scheduled workflow tests
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_explore_workflow():
    """Explorer should be able to fetch feeds and generate a briefing."""
    from fetcher import fetch_sources
    # Fetch a small source (use fetch_sources with specific source, not fetch_all)
    items = fetch_sources(["hackernews"])
    assert len(items) > 0, "Fetcher returned no items from hackernews"
    # Verify items have expected structure
    for item in items[:5]:
        assert "title" in item, f"Feed item missing title: {item}"
        assert "source" in item, f"Feed item missing source: {item}"


@pytest.mark.slow
def test_task_routing():
    """Task planner should route different messages to appropriate agents."""
    from sub_agent import claude_think
    from agent_registry import AgentRegistry

    registry = AgentRegistry()
    agent_descriptions = registry.get_agent_descriptions()

    test_cases = [
        ("帮我写一篇文章", "writer"),
        ("solve this integral", "researcher"),
        ("分析AAPL股票", "analyst"),
        ("写一个Python脚本", "coder"),
    ]

    for msg, expected_agent in test_cases:
        prompt = f"""Given this message, which agent should handle it?
Available agents:
{agent_descriptions}

Message: {msg}

Reply with ONLY the agent name, nothing else."""
        result = claude_think(prompt, timeout=30).strip().lower()
        assert expected_agent in result, (
            f"Routing '{msg}' → expected '{expected_agent}', got '{result}'"
        )


# ---------------------------------------------------------------------------
# Config validation test
# ---------------------------------------------------------------------------

def test_config_validates():
    """Config validation should pass in the real environment."""
    from config import validate_config, LOGS_DIR
    if not LOGS_DIR.exists():
        return  # Skip in CI — local paths not available
    assert validate_config(), "Config validation failed — check paths"
