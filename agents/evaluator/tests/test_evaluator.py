"""Evaluator agent tests."""
from __future__ import annotations
import json
import sys
from pathlib import Path

import pytest

_AGENTS = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS / "evaluator"))
sys.path.insert(0, str(_AGENTS / "super"))
sys.path.insert(0, str(_AGENTS.parent / "lib"))


def _load_evaluator_handler():
    """Import evaluator handler explicitly to avoid sys.path collisions."""
    import importlib.util
    handler_path = _AGENTS / "evaluator" / "handler.py"
    spec = importlib.util.spec_from_file_location("evaluator_handler", handler_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_handler_imports():
    handler = _load_evaluator_handler()
    assert callable(handler.handle)
    assert callable(handler.score_all)
    assert callable(handler.score_agent)
    assert callable(handler.score_super)
    assert callable(handler.diagnose_and_improve)


def test_manifest_valid():
    manifest = json.loads(
        (Path(__file__).parent.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "evaluator"


def test_all_agents_have_criteria():
    """Every agent with a manifest should have evaluation criteria defined."""
    _handler = _load_evaluator_handler()
    AGENT_CRITERIA = _handler.AGENT_CRITERIA
    from agent_registry import AgentRegistry
    registry = AgentRegistry()
    # Agents that don't need criteria (meta-agents, scheduler-only)
    exempt = {"evaluator", "reader", "surfer", "photo", "video", "health"}
    for name in registry.list_agents():
        if name in exempt:
            continue
        assert name in AGENT_CRITERIA, \
            f"Agent '{name}' has no evaluation criteria in AGENT_CRITERIA"


def test_criteria_have_metrics():
    """Each agent's criteria should have at least 2 metrics."""
    _handler = _load_evaluator_handler()
    AGENT_CRITERIA = _handler.AGENT_CRITERIA
    for name, criteria in AGENT_CRITERIA.items():
        assert "metrics" in criteria, f"{name} criteria missing 'metrics'"
        assert len(criteria["metrics"]) >= 2, \
            f"{name} has only {len(criteria['metrics'])} metrics (need 2+)"


def test_score_agent_empty():
    """Scoring an agent with no history should return gracefully."""
    _h = _load_evaluator_handler()
    result = _h.score_agent("coder", days=0)
    assert result["task_count"] == 0
    assert "note" in result


def test_score_super():
    """Super scoring should work even with minimal data."""
    _h = _load_evaluator_handler()
    result = _h.score_super(days=1)
    assert "scores" in result
    assert "crash_rate" in result["scores"]


def test_score_all():
    """Full assessment should complete without errors."""
    _h = _load_evaluator_handler()
    result = _h.score_all(days=7)
    assert "agents" in result
    assert "super" in result
    assert "aggregate" in result
    assert "generated_at" in result


def test_no_llm_self_eval_in_scoring():
    """Scoring functions should NOT call any LLM — all deterministic."""
    handler = _load_evaluator_handler()
    source = Path(handler.__file__).read_text(encoding="utf-8")
    # score_agent and score_super should not use claude_think/act
    # (diagnose_and_improve uses model_think, which is OK — it's a separate step)
    score_funcs = ["def score_agent", "def score_super", "def score_all"]
    for func_name in score_funcs:
        start = source.find(func_name)
        # Find the next def (end of function)
        next_def = source.find("\ndef ", start + 10)
        func_body = source[start:next_def] if next_def > start else source[start:]
        assert "claude_think" not in func_body, \
            f"{func_name} should not call claude_think (must be deterministic)"
        assert "claude_act" not in func_body, \
            f"{func_name} should not call claude_act (must be deterministic)"


@pytest.mark.slow
def test_full_assessment_report():
    """Run full assessment and verify report structure."""
    import tempfile, uuid
    from handler import handle
    ws = Path(tempfile.mkdtemp(prefix="mira_eval_test_"))
    result = handle(
        workspace=ws,
        task_id=f"test_{uuid.uuid4().hex[:8]}",
        content="Run assessment days=7",
        sender="ang",
        thread_id="",
    )
    assert result, "Evaluator returned empty"
    assert "Aggregate" in result
    assert "Per-Agent" in result
