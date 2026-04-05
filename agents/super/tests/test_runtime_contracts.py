from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SUPER = _HERE.parent
_AGENTS = _SUPER.parent
sys.path.insert(0, str(_SUPER))
sys.path.insert(0, str(_AGENTS / "shared"))

from agent_registry import AgentRegistry
import task_worker


def _patch_task_worker_test_side_effects(monkeypatch):
    import soul_manager

    monkeypatch.setattr(task_worker, "_emit_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_worker, "_append_exec_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_worker, "_record_premortem", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_worker, "_record_postmortem", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_worker, "_verify_output", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_worker, "save_episode", lambda *args, **kwargs: None)
    monkeypatch.setattr(soul_manager, "auto_flush", lambda *args, **kwargs: None)


def test_writer_handler_matches_production_contract(tmp_path, monkeypatch):
    """Writer must accept task-worker args and materialize output.md."""
    registry = AgentRegistry()
    handler = registry.load_handler("writer")
    project_dir = tmp_path / "writer_project"
    project_dir.mkdir()
    final_file = project_dir / "final.md"
    final_file.write_text("# Test Title\n\nFinal draft body.", encoding="utf-8")

    def fake_pipeline(title: str, body: str):
        return project_dir, final_file.read_text(encoding="utf-8")

    monkeypatch.setitem(handler.__globals__, "run_full_pipeline", fake_pipeline)

    workspace = tmp_path / "task"
    workspace.mkdir()
    summary = handler(workspace, "task123", "写一篇关于测试修复的文章", "ang", "thread1")

    assert summary
    assert "Writing project complete" in summary
    assert (workspace / "output.md").read_text(encoding="utf-8").startswith("# Test Title")
    assert (workspace / "project_path.txt").read_text(encoding="utf-8") == str(project_dir)


def test_execute_plan_steps_backfills_needs_input_result(tmp_path, monkeypatch):
    """Handler return prefixes like NEEDS_APPROVAL must become result.json."""
    workspace = tmp_path / "task"
    workspace.mkdir()

    class FakeRegistry:
        def load_handler(self, name: str):
            assert name == "socialmedia"

            def handler(ws, task_id, instruction, sender, thread_id):
                (ws / "output.md").write_text("Confirm publish?", encoding="utf-8")
                return "NEEDS_APPROVAL:Confirm publish?"

            return handler

    monkeypatch.setattr("agent_registry.get_registry", lambda: FakeRegistry())
    _patch_task_worker_test_side_effects(monkeypatch)

    plan = [{
        "agent": "socialmedia",
        "instruction": "Publish it",
        "tier": "light",
        "prediction": {"difficulty": "easy", "failure_modes": [], "success_criteria": "approval requested"},
    }]
    task_worker._execute_plan_steps(
        plan,
        workspace,
        "task123",
        "把文章发到 Substack",
        "ang",
        "thread1",
        None,
        False,
        1,
    )

    result = json.loads((workspace / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "needs-input"
    assert result["summary"] == "Confirm publish?"
    assert result["agent"] == "socialmedia"


def test_execute_plan_steps_backfills_done_result(tmp_path, monkeypatch):
    """Handlers that only return output/summary should still produce result.json."""
    workspace = tmp_path / "task"
    workspace.mkdir()

    class FakeRegistry:
        def load_handler(self, name: str):
            assert name == "writer"

            def handler(ws, task_id, instruction, sender, thread_id):
                (ws / "output.md").write_text("# Draft\n\nBody", encoding="utf-8")
                (ws / "summary.txt").write_text("Draft ready", encoding="utf-8")
                return "Draft ready"

            return handler

    monkeypatch.setattr("agent_registry.get_registry", lambda: FakeRegistry())
    _patch_task_worker_test_side_effects(monkeypatch)

    plan = [{
        "agent": "writer",
        "instruction": "Write a draft",
        "tier": "light",
        "prediction": {"difficulty": "easy", "failure_modes": [], "success_criteria": "draft returned"},
    }]
    task_worker._execute_plan_steps(
        plan,
        workspace,
        "task124",
        "写一篇短文",
        "ang",
        "thread1",
        None,
        False,
        1,
    )

    result = json.loads((workspace / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "done"
    assert result["summary"] == "Draft ready"
    assert result["agent"] == "writer"


def test_execute_plan_steps_passes_tier_and_thread_context(tmp_path, monkeypatch):
    """Registry dispatch should pass supported runtime kwargs to handlers."""
    workspace = tmp_path / "task"
    workspace.mkdir()

    captured = {}

    class FakeRegistry:
        def load_handler(self, name: str):
            assert name == "discussion"

            def handler(ws, task_id, instruction, sender, thread_id, **kwargs):
                captured["tier"] = kwargs.get("tier")
                captured["thread_history"] = kwargs.get("thread_history")
                captured["thread_memory"] = kwargs.get("thread_memory")
                (ws / "output.md").write_text("Reply", encoding="utf-8")
                (ws / "summary.txt").write_text("Reply", encoding="utf-8")
                return "Reply"

            return handler

    monkeypatch.setattr("agent_registry.get_registry", lambda: FakeRegistry())
    monkeypatch.setattr(task_worker, "load_thread_history", lambda thread_id: "history block")
    monkeypatch.setattr(task_worker, "load_thread_memory", lambda thread_id: "memory block")
    _patch_task_worker_test_side_effects(monkeypatch)

    plan = [{
        "agent": "discussion",
        "instruction": "Think deeply",
        "tier": "heavy",
        "prediction": {"difficulty": "medium", "failure_modes": [], "success_criteria": "reply returned"},
    }]
    task_worker._execute_plan_steps(
        plan,
        workspace,
        "task125",
        "聊聊这个问题",
        "ang",
        "thread99",
        None,
        False,
        1,
    )

    assert captured["tier"] == "heavy"
    assert captured["thread_history"] == "history block"
    assert captured["thread_memory"] == "memory block"
