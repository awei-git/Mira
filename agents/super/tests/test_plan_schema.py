"""Tests for plan step validation and alias normalization."""
import sys
from pathlib import Path

_SUPER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SUPER))
sys.path.insert(0, str(_SUPER.parent / "shared"))


def test_normalize_canonical_name():
    from task_worker import _normalize_agent_name
    valid = {"writer", "explorer", "general", "socialmedia"}
    name, alias = _normalize_agent_name("writer", valid)
    assert name == "writer"
    assert alias is None  # no normalization needed


def test_normalize_alias():
    from task_worker import _normalize_agent_name
    valid = {"writer", "explorer", "general", "socialmedia"}
    name, alias = _normalize_agent_name("writing", valid)
    assert name == "writer"
    assert alias == "writing"


def test_normalize_publish_alias():
    from task_worker import _normalize_agent_name
    valid = {"writer", "explorer", "general", "socialmedia"}
    name, alias = _normalize_agent_name("publish", valid)
    assert name == "socialmedia"
    assert alias == "publish"


def test_normalize_briefing_alias():
    from task_worker import _normalize_agent_name
    valid = {"writer", "explorer", "general", "socialmedia"}
    name, alias = _normalize_agent_name("briefing", valid)
    assert name == "explorer"
    assert alias == "briefing"


def test_normalize_unknown_agent():
    from task_worker import _normalize_agent_name
    valid = {"writer", "explorer", "general"}
    name, alias = _normalize_agent_name("nonexistent", valid)
    assert name is None
    assert alias == "nonexistent"


def test_validate_plan_step_valid():
    from task_worker import _validate_plan_step
    valid = {"writer", "explorer", "general", "socialmedia"}
    step = {"agent": "writer", "instruction": "Write an article", "tier": "heavy"}
    result = _validate_plan_step(step, valid)
    assert result is not None
    assert result["agent"] == "writer"
    assert result["tier"] == "heavy"


def test_validate_plan_step_alias_resolved():
    from task_worker import _validate_plan_step
    valid = {"writer", "explorer", "general", "socialmedia"}
    step = {"agent": "writing", "instruction": "Write something"}
    result = _validate_plan_step(step, valid)
    assert result is not None
    assert result["agent"] == "writer"  # alias resolved


def test_validate_plan_step_invalid_agent():
    from task_worker import _validate_plan_step
    valid = {"writer", "explorer", "general"}
    step = {"agent": "nonexistent", "instruction": "Do something"}
    result = _validate_plan_step(step, valid)
    assert result is None


def test_validate_plan_step_empty_instruction():
    from task_worker import _validate_plan_step
    valid = {"writer", "general"}
    step = {"agent": "writer", "instruction": ""}
    result = _validate_plan_step(step, valid)
    assert result is None


def test_validate_plan_step_default_tier():
    from task_worker import _validate_plan_step
    valid = {"general"}
    step = {"agent": "general", "instruction": "Do something"}
    result = _validate_plan_step(step, valid)
    assert result["tier"] == "light"  # default


def test_validate_plan_step_with_prediction():
    from task_worker import _validate_plan_step
    valid = {"general"}
    step = {
        "agent": "general",
        "instruction": "Search for info",
        "tier": "light",
        "prediction": {
            "difficulty": "easy",
            "failure_modes": ["timeout", "no results"],
            "success_criteria": "found relevant info",
        },
    }
    result = _validate_plan_step(step, valid)
    assert result is not None
    assert result["prediction"]["difficulty"] == "easy"
    assert len(result["prediction"]["failure_modes"]) == 2
