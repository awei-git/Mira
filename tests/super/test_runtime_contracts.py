from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_AGENTS = Path(__file__).resolve().parent.parent.parent / "agents"

from agent_registry import AgentRegistry
import task_worker
import task_support
import handlers_legacy
from execution.plan_state import initialize_plan_artifacts, mark_step_finished


def _patch_task_worker_test_side_effects(monkeypatch):
    import memory.soul as soul_manager

    monkeypatch.setattr(task_worker, "_emit_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_support, "_append_exec_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_worker, "_record_premortem", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_worker, "_record_postmortem", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_support, "_verify_output", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_worker, "save_episode", lambda *args, **kwargs: None)
    monkeypatch.setattr(soul_manager, "auto_flush", lambda *args, **kwargs: None)


class _RegistryPolicyMixin:
    capability_class = "local-write"
    policy_requires_preflight = False
    policy_fail_closed = False
    policy_allow_fallback_to_general = True

    def get_capability_class(self, name: str):
        return self.capability_class

    def get_capability_policy(self, name: str):
        return {
            "capability_class": self.capability_class,
            "requires_preflight": self.policy_requires_preflight,
            "requires_approval": False,
            "requires_verification": True,
            "fail_closed": self.policy_fail_closed,
            "allow_fallback_to_general": self.policy_allow_fallback_to_general,
            "auto_retry": True,
        }


def test_writer_handler_matches_production_contract(tmp_path, monkeypatch):
    """Writer must accept task-worker args and materialize output.md."""
    registry = AgentRegistry()
    handler = registry.load_handler("writer")
    project_dir = tmp_path / "writer_project"
    project_dir.mkdir()
    final_file = project_dir / "final.md"
    final_file.write_text("# Test Title\n\nFinal draft body.", encoding="utf-8")

    def fake_pipeline(title: str, body: str, *, persona_prompt: str = "", context_note: str = ""):
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

    class FakeRegistry(_RegistryPolicyMixin):
        def load_handler(self, name: str):
            assert name == "socialmedia"

            def handler(ws, task_id, instruction, sender, thread_id):
                (ws / "output.md").write_text("Confirm publish?", encoding="utf-8")
                return "NEEDS_APPROVAL:Confirm publish?"

            return handler

    monkeypatch.setattr("agent_registry.get_registry", lambda: FakeRegistry())
    _patch_task_worker_test_side_effects(monkeypatch)

    plan = [
        {
            "agent": "socialmedia",
            "instruction": "Publish it",
            "tier": "light",
            "prediction": {"difficulty": "easy", "failure_modes": [], "success_criteria": "approval requested"},
        }
    ]
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
    assert result["step_id"] == "step-01"
    assert result["failure_class"] == "approval_required"
    assert result["next_action"] == "await-user-input"
    assert result["retry_count"] == 0
    assert result["verification"]["status"] == "verified"
    assert any(Path(item["path"]).name == "output.md" for item in result["artifacts_produced"])


def test_execute_plan_steps_backfills_done_result(tmp_path, monkeypatch):
    """Handlers that only return output/summary should still produce result.json."""
    workspace = tmp_path / "task"
    workspace.mkdir()

    class FakeRegistry(_RegistryPolicyMixin):
        def load_handler(self, name: str):
            assert name == "writer"

            def handler(ws, task_id, instruction, sender, thread_id):
                (ws / "output.md").write_text("# Draft\n\nBody", encoding="utf-8")
                (ws / "summary.txt").write_text("Draft ready", encoding="utf-8")
                return "Draft ready"

            return handler

    monkeypatch.setattr("agent_registry.get_registry", lambda: FakeRegistry())
    _patch_task_worker_test_side_effects(monkeypatch)

    plan = [
        {
            "agent": "writer",
            "instruction": "Write a draft",
            "tier": "light",
            "prediction": {"difficulty": "easy", "failure_modes": [], "success_criteria": "draft returned"},
        }
    ]
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
    assert result["step_id"] == "step-01"
    assert result["failure_class"] == ""
    assert result["next_action"] == "proceed-to-next-step"
    assert result["verification"]["status"] == "verified"
    assert any(Path(item["path"]).name == "output.md" for item in result["artifacts_produced"])
    assert any(Path(item["path"]).name == "summary.txt" for item in result["artifacts_produced"])


def test_execute_plan_steps_rewrites_done_without_output_to_error(tmp_path, monkeypatch):
    """A handler cannot claim done without producing a verifiable artifact."""
    workspace = tmp_path / "task"
    workspace.mkdir()

    class FakeRegistry(_RegistryPolicyMixin):
        capability_class = "read-only"

        def load_handler(self, name: str):
            assert name == "general"

            def handler(ws, task_id, instruction, sender, thread_id):
                (ws / "result.json").write_text(
                    json.dumps(
                        {
                            "task_id": task_id,
                            "status": "done",
                            "summary": "Claimed success",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                return "Claimed success"

            return handler

    monkeypatch.setattr("agent_registry.get_registry", lambda: FakeRegistry())
    _patch_task_worker_test_side_effects(monkeypatch)

    plan = [
        {
            "agent": "general",
            "instruction": "Do something",
            "tier": "light",
            "prediction": {"difficulty": "easy", "failure_modes": [], "success_criteria": "done"},
        }
    ]
    task_worker._execute_plan_steps(
        plan,
        workspace,
        "task126",
        "做点事情",
        "ang",
        "thread1",
        None,
        False,
        1,
    )

    result = json.loads((workspace / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert "no verifiable output" in result["summary"]
    assert result["failure_class"] == "verification_failed"
    assert result["next_action"] == "inspect-artifacts-and-retry"
    assert result["verification"]["status"] == "failed"
    assert result["step_id"] == "step-01"


def test_execute_plan_steps_passes_tier_and_thread_context(tmp_path, monkeypatch):
    """Registry dispatch should pass supported runtime kwargs to handlers."""
    workspace = tmp_path / "task"
    workspace.mkdir()

    captured = {}

    class FakeRegistry(_RegistryPolicyMixin):
        capability_class = "read-only"

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
    monkeypatch.setattr(task_support, "load_thread_history", lambda thread_id: "history block")
    monkeypatch.setattr(task_support, "load_thread_memory", lambda thread_id: "memory block")
    _patch_task_worker_test_side_effects(monkeypatch)

    plan = [
        {
            "agent": "discussion",
            "instruction": "Think deeply",
            "tier": "heavy",
            "prediction": {"difficulty": "medium", "failure_modes": [], "success_criteria": "reply returned"},
        }
    ]
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


def test_execute_plan_steps_respects_registry_preflight(tmp_path, monkeypatch):
    """Registry preflight can block execution before the handler runs."""
    workspace = tmp_path / "task"
    workspace.mkdir()
    called = {"handler": False}

    class FakeRegistry(_RegistryPolicyMixin):
        capability_class = "read-only"

        def load_preflight(self, name: str):
            assert name == "writer"
            return lambda ws, task_id, instruction, sender, thread_id, **kwargs: (
                False,
                "PREFLIGHT BLOCKED [file_write]: missing content",
            )

        def load_handler(self, name: str):
            assert name == "writer"

            def handler(ws, task_id, instruction, sender, thread_id, **kwargs):
                called["handler"] = True
                return "should not run"

            return handler

    monkeypatch.setattr("agent_registry.get_registry", lambda: FakeRegistry())
    _patch_task_worker_test_side_effects(monkeypatch)

    plan = [
        {
            "agent": "writer",
            "instruction": "",
            "tier": "light",
            "prediction": {"difficulty": "easy", "failure_modes": [], "success_criteria": "blocked"},
        }
    ]
    task_worker._execute_plan_steps(
        plan,
        workspace,
        "task127",
        "",
        "ang",
        "thread1",
        None,
        False,
        1,
    )

    result = json.loads((workspace / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert "PREFLIGHT BLOCKED" in result["summary"]
    assert result["failure_class"] == "preflight_blocked"
    assert result["next_action"] == "resolve-preflight-block"
    assert result["verification"]["status"] == "not-run"
    assert result["step_id"] == "step-01"
    assert called["handler"] is False


def test_execute_plan_steps_preflight_classification_does_not_depend_on_message_text(tmp_path, monkeypatch):
    workspace = tmp_path / "task"
    workspace.mkdir()

    class FakeRegistry(_RegistryPolicyMixin):
        capability_class = "read-only"

        def load_preflight(self, name: str):
            assert name == "writer"
            return lambda ws, task_id, instruction, sender, thread_id, **kwargs: (
                False,
                "missing source material",
            )

        def load_handler(self, name: str):
            raise AssertionError("handler should not run when preflight blocks")

    monkeypatch.setattr("agent_registry.get_registry", lambda: FakeRegistry())
    _patch_task_worker_test_side_effects(monkeypatch)

    task_worker._execute_plan_steps(
        [
            {
                "agent": "writer",
                "instruction": "write it",
                "tier": "light",
                "prediction": {"difficulty": "easy", "failure_modes": [], "success_criteria": "blocked"},
            }
        ],
        workspace,
        "task127b",
        "write it",
        "ang",
        "thread1",
        None,
        False,
        1,
    )

    result = json.loads((workspace / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert result["summary"] == "missing source material"
    assert result["failure_class"] == "preflight_blocked"
    assert result["next_action"] == "resolve-preflight-block"


def test_execute_plan_steps_fails_closed_on_preflight_exception(tmp_path, monkeypatch):
    """Preflight exceptions must fail closed, not fall back to general."""
    workspace = tmp_path / "task"
    workspace.mkdir()
    called = {"handler": False}

    class FakeRegistry(_RegistryPolicyMixin):
        policy_requires_preflight = True
        policy_fail_closed = True
        policy_allow_fallback_to_general = False

        def load_preflight(self, name: str):
            assert name == "writer"

            def preflight(ws, task_id, instruction, sender, thread_id, **kwargs):
                raise RuntimeError("boom")

            return preflight

        def load_handler(self, name: str):
            assert name == "writer"

            def handler(ws, task_id, instruction, sender, thread_id, **kwargs):
                called["handler"] = True
                return "should not run"

            return handler

    monkeypatch.setattr("agent_registry.get_registry", lambda: FakeRegistry())
    _patch_task_worker_test_side_effects(monkeypatch)

    plan = [
        {
            "agent": "writer",
            "instruction": "write it",
            "tier": "light",
            "prediction": {"difficulty": "easy", "failure_modes": [], "success_criteria": "blocked"},
        }
    ]
    task_worker._execute_plan_steps(
        plan,
        workspace,
        "task128",
        "write it",
        "ang",
        "thread1",
        None,
        False,
        1,
    )

    result = json.loads((workspace / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert "preflight failed" in result["summary"]
    assert called["handler"] is False


def test_execute_plan_steps_falls_back_when_preflight_load_errors(tmp_path, monkeypatch):
    """Registry preflight import/load errors should follow normal general fallback."""
    workspace = tmp_path / "task"
    workspace.mkdir()
    called = {"general": False, "handler": False}

    class FakeRegistry(_RegistryPolicyMixin):
        def load_preflight(self, name: str):
            assert name == "writer"
            raise ImportError("bad preflight import")

        def load_handler(self, name: str):
            called["handler"] = True
            raise AssertionError("handler should not load after preflight import error")

    def fake_general(ws, task_id, instruction, sender, thread_id, **kwargs):
        called["general"] = True
        (ws / "output.md").write_text("Fallback reply", encoding="utf-8")
        (ws / "summary.txt").write_text("Fallback reply", encoding="utf-8")
        task_worker._write_result(ws, task_id, "done", "Fallback reply", agent="general")

    monkeypatch.setattr("agent_registry.get_registry", lambda: FakeRegistry())
    monkeypatch.setattr(handlers_legacy, "_handle_general", fake_general)
    _patch_task_worker_test_side_effects(monkeypatch)

    plan = [
        {
            "agent": "writer",
            "instruction": "write it",
            "tier": "light",
            "prediction": {"difficulty": "easy", "failure_modes": [], "success_criteria": "fallback"},
        }
    ]
    task_worker._execute_plan_steps(
        plan,
        workspace,
        "task128b",
        "write it",
        "ang",
        "thread1",
        None,
        False,
        1,
    )

    result = json.loads((workspace / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "done"
    assert result["agent"] == "general"
    assert called["general"] is True
    assert called["handler"] is False


def test_execute_plan_steps_blocks_when_required_preflight_load_fails(tmp_path, monkeypatch):
    workspace = tmp_path / "task"
    workspace.mkdir()
    called = {"general": False, "handler": False}

    class FakeRegistry(_RegistryPolicyMixin):
        policy_requires_preflight = True
        policy_fail_closed = True
        policy_allow_fallback_to_general = False

        def requires_preflight(self, name: str):
            assert name == "writer"
            return True

        def load_preflight(self, name: str):
            assert name == "writer"
            raise ImportError("bad preflight import")

        def load_handler(self, name: str):
            called["handler"] = True
            raise AssertionError("handler should not load after required preflight import error")

    def fake_general(ws, task_id, instruction, sender, thread_id, **kwargs):
        called["general"] = True
        raise AssertionError("general fallback should not run for required-preflight agents")

    monkeypatch.setattr("agent_registry.get_registry", lambda: FakeRegistry())
    monkeypatch.setattr(handlers_legacy, "_handle_general", fake_general)
    _patch_task_worker_test_side_effects(monkeypatch)

    task_worker._execute_plan_steps(
        [
            {
                "agent": "writer",
                "instruction": "write it",
                "tier": "light",
                "prediction": {"difficulty": "easy", "failure_modes": [], "success_criteria": "blocked"},
            }
        ],
        workspace,
        "task128c",
        "write it",
        "ang",
        "thread1",
        None,
        False,
        1,
    )

    result = json.loads((workspace / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert "preflight load failed" in result["summary"]
    assert result["agent"] == "writer"
    assert called["general"] is False
    assert called["handler"] is False


def test_execute_plan_steps_writes_plan_and_step_state_artifacts(tmp_path, monkeypatch):
    workspace = tmp_path / "task"
    workspace.mkdir()

    class FakeRegistry(_RegistryPolicyMixin):
        def load_handler(self, name: str):
            assert name == "writer"

            def handler(ws, task_id, instruction, sender, thread_id, **kwargs):
                (ws / "output.md").write_text("# Draft\n\nBody", encoding="utf-8")
                (ws / "summary.txt").write_text("Draft ready", encoding="utf-8")
                return "Draft ready"

            return handler

    monkeypatch.setattr("agent_registry.get_registry", lambda: FakeRegistry())
    _patch_task_worker_test_side_effects(monkeypatch)

    plan = [
        {
            "agent": "writer",
            "instruction": "Write a draft",
            "tier": "light",
            "capability_class": "local-write",
            "policy": {
                "capability_class": "local-write",
                "requires_preflight": False,
                "requires_approval": False,
                "requires_verification": True,
                "fail_closed": False,
                "allow_fallback_to_general": True,
                "auto_retry": True,
            },
            "prediction": {"difficulty": "easy", "failure_modes": [], "success_criteria": "draft returned"},
        }
    ]
    task_worker._execute_plan_steps(
        plan,
        workspace,
        "task129a",
        "写一篇短文",
        "ang",
        "thread1",
        None,
        False,
        1,
    )

    plan_artifact = json.loads((workspace / "plan.json").read_text(encoding="utf-8"))
    step_state = json.loads((workspace / "step_states.json").read_text(encoding="utf-8"))
    result = json.loads((workspace / "result.json").read_text(encoding="utf-8"))

    assert plan_artifact["task_id"] == "task129a"
    assert plan_artifact["workflow_id"] == "task129a"
    assert plan_artifact["steps"][0]["capability_class"] == "local-write"
    assert step_state["status"] == "done"
    assert step_state["workflow_id"] == "task129a"
    assert step_state["steps"][0]["status"] == "done"
    assert step_state["steps"][0]["declared_agent"] == "writer"
    assert step_state["steps"][0]["execution_agent"] == "writer"
    assert result["capability_class"] == "local-write"
    assert result["step_id"] == "step-01"
    assert result["verification"]["status"] == "verified"


def test_write_result_backfills_canonical_contract_for_legacy_calls(tmp_path, monkeypatch):
    workspace = tmp_path / "task"
    workspace.mkdir()
    _patch_task_worker_test_side_effects(monkeypatch)

    (workspace / "output.md").write_text("Legacy output", encoding="utf-8")
    task_worker._write_result(workspace, "task129z", "done", "Legacy output", agent="general")

    result = json.loads((workspace / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "done"
    assert result["workflow_id"] == "task129z"
    assert result["step_id"] == ""
    assert result["failure_class"] == ""
    assert result["next_action"] == "proceed-to-next-step"
    assert result["verification"]["status"] == "not-run"
    assert any(Path(item["path"]).name == "output.md" for item in result["artifacts_produced"])


def test_ensure_step_result_reuses_cached_verification(tmp_path, monkeypatch):
    workspace = tmp_path / "task"
    workspace.mkdir()
    (workspace / "output.md").write_text("Cached output", encoding="utf-8")
    (workspace / "result.json").write_text(
        json.dumps({"task_id": "task129za", "status": "done", "summary": "Cached output"}, ensure_ascii=False),
        encoding="utf-8",
    )

    calls = {"count": 0}

    class FakeVerify:
        verified = True
        artifact_type = "file"
        checks = []

        def summary(self):
            return "VERIFY VERIFIED [file]: ok"

    def fake_verify(*args, **kwargs):
        calls["count"] += 1
        return FakeVerify()

    import task_result

    monkeypatch.setattr(task_result, "verify_artifact", fake_verify)

    task_worker._ensure_step_result(
        workspace,
        "task129za",
        "writer",
        "Write it",
        "Cached output",
        None,
        metadata={"step_index": 0, "step_id": "step-01"},
    )

    result = json.loads((workspace / "result.json").read_text(encoding="utf-8"))
    assert calls["count"] == 1
    assert result["verification"]["status"] == "verified"
    assert result["workflow_id"] == "task129za"


def test_write_result_only_collects_declared_public_artifacts(tmp_path, monkeypatch):
    workspace = tmp_path / "task"
    workspace.mkdir()
    _patch_task_worker_test_side_effects(monkeypatch)

    (workspace / "output.md").write_text("Public output", encoding="utf-8")
    (workspace / "summary.txt").write_text("Summary", encoding="utf-8")
    (workspace / "scratchpad.md").write_text("Private scratch", encoding="utf-8")
    (workspace / "notes.txt").write_text("Explicit artifact", encoding="utf-8")

    task_worker._write_result(
        workspace,
        "task129zb",
        "done",
        "Public output",
        agent="writer",
        metadata={"artifacts_expected": ["notes.txt"]},
    )

    result = json.loads((workspace / "result.json").read_text(encoding="utf-8"))
    artifact_names = {Path(item["path"]).name for item in result["artifacts_produced"]}
    assert artifact_names == {"output.md", "summary.txt", "notes.txt"}
    assert "scratchpad.md" not in artifact_names
    assert result["workflow_id"] == "task129zb"


def test_execute_plan_steps_marks_fallback_exception_as_failed(tmp_path, monkeypatch):
    workspace = tmp_path / "task"
    workspace.mkdir()

    class FakeRegistry(_RegistryPolicyMixin):
        def load_preflight(self, name: str):
            raise ImportError("bad preflight import")

        def load_handler(self, name: str):
            raise AssertionError("handler should not load after preflight import error")

    def fake_general(ws, task_id, instruction, sender, thread_id, **kwargs):
        raise RuntimeError("fallback exploded")

    monkeypatch.setattr("agent_registry.get_registry", lambda: FakeRegistry())
    monkeypatch.setattr(handlers_legacy, "_handle_general", fake_general)
    _patch_task_worker_test_side_effects(monkeypatch)

    task_worker._execute_plan_steps(
        [
            {
                "agent": "writer",
                "instruction": "write it",
                "tier": "light",
                "prediction": {"difficulty": "easy", "failure_modes": [], "success_criteria": "error"},
            }
        ],
        workspace,
        "task129b",
        "write it",
        "ang",
        "thread1",
        None,
        False,
        1,
    )

    result = json.loads((workspace / "result.json").read_text(encoding="utf-8"))
    step_state = json.loads((workspace / "step_states.json").read_text(encoding="utf-8"))

    assert result["status"] == "failed"
    assert "general fallback failed" in result["summary"]
    assert result["agent"] == "general"
    assert step_state["status"] == "failed"
    assert step_state["steps"][0]["status"] == "failed"


def test_execute_plan_steps_initializes_artifacts_once(tmp_path, monkeypatch):
    workspace = tmp_path / "task"
    workspace.mkdir()
    calls = []

    import plan_executor

    original_initialize = plan_executor.initialize_plan_artifacts

    def tracked_initialize(*args, **kwargs):
        calls.append(kwargs["task_id"])
        return original_initialize(*args, **kwargs)

    class FakeRegistry(_RegistryPolicyMixin):
        def load_handler(self, name: str):
            def handler(ws, task_id, instruction, sender, thread_id, **kwargs):
                (ws / "output.md").write_text("done", encoding="utf-8")
                return "done"

            return handler

    monkeypatch.setattr(plan_executor, "initialize_plan_artifacts", tracked_initialize)
    monkeypatch.setattr("agent_registry.get_registry", lambda: FakeRegistry())
    _patch_task_worker_test_side_effects(monkeypatch)

    plan = [
        {
            "agent": "writer",
            "instruction": "Write a draft",
            "tier": "light",
            "prediction": {"difficulty": "easy", "failure_modes": [], "success_criteria": "draft returned"},
        }
    ]
    task_worker._execute_plan(plan, workspace, "task129c", "写一篇短文", "ang", "thread1")

    assert calls == ["task129c"]


def test_mark_step_finished_preserves_terminal_plan_status(tmp_path):
    workspace = tmp_path / "task"
    workspace.mkdir()
    plan = [
        {"agent": "writer", "instruction": "step one", "prediction": {}},
        {"agent": "writer", "instruction": "step two", "prediction": {}},
    ]
    initialize_plan_artifacts(
        workspace,
        task_id="task129d",
        workflow_id="task129d",
        user_id="ang",
        request="two step task",
        plan=plan,
    )

    mark_step_finished(
        workspace,
        step_index=0,
        status="error",
        declared_agent="writer",
        execution_agent="writer",
        failure_reason="boom",
    )
    mark_step_finished(
        workspace,
        step_index=1,
        status="pending",
        declared_agent="writer",
        execution_agent="writer",
    )

    step_state = json.loads((workspace / "step_states.json").read_text(encoding="utf-8"))
    assert step_state["status"] == "failed"


def test_socialmedia_handle_reuses_preflight_cache(tmp_path, monkeypatch):
    """Execution should use the exact plan/content that preflight approved."""
    registry = AgentRegistry()
    handler = registry.load_handler("socialmedia")
    preflight = registry.load_preflight("socialmedia")
    assert preflight is not None

    module_globals = handler.__globals__
    monkeypatch.setitem(
        module_globals,
        "_plan_publish",
        lambda content: {
            "platform": "substack",
            "source": "article.md",
            "title": "Approved Title",
            "subtitle": "",
        },
    )
    monkeypatch.setitem(module_globals, "_resolve_content", lambda source, content: "# Approved\n\nBody" * 40)
    monkeypatch.setitem(module_globals, "MIRA_ROOT", tmp_path)

    # Mock publish_to_substack so test never hits the real API.
    # 2026-04-07: full autonomy means handler calls publish_to_substack directly;
    # any unmocked test that reaches this path would actually publish to Substack.
    import substack as _substack_mod

    publish_calls = []

    def _fake_publish(title, subtitle, article_text, workspace):
        publish_calls.append({"title": title, "subtitle": subtitle, "chars": len(article_text)})
        return f"[TEST] Would publish '{title}'"

    monkeypatch.setattr(_substack_mod, "publish_to_substack", _fake_publish)

    workspace = tmp_path / "task"
    workspace.mkdir()

    passed, msg = preflight(workspace, "task129", "publish it", "ang", "thread1")
    assert passed, msg

    monkeypatch.setitem(
        module_globals,
        "_plan_publish",
        lambda content: (_ for _ in ()).throw(AssertionError("handle should reuse preflight cache")),
    )
    monkeypatch.setitem(
        module_globals,
        "_resolve_content",
        lambda source, content: (_ for _ in ()).throw(AssertionError("handle should reuse preflight cache")),
    )

    result = handler(workspace, "task129", "publish it", "ang", "thread1")

    assert result is not None
    # Full autonomy: handler auto-publishes via publish_to_substack (mocked).
    assert publish_calls, "handler should have called publish_to_substack"
    assert publish_calls[0]["title"] == "Approved Title"
    assert result.startswith("[TEST] Would publish")


def test_podcast_preflight_requires_real_article(tmp_path):
    registry = AgentRegistry()
    preflight = registry.load_preflight("podcast")
    assert preflight is not None

    workspace = tmp_path / "podcast"
    workspace.mkdir()

    passed, msg = preflight(workspace, "task130", "make a podcast", "ang", "thread1")
    assert passed is False
    assert "PREFLIGHT BLOCKED [podcast]" in msg


def test_podcast_preflight_accepts_workspace_article(tmp_path):
    registry = AgentRegistry()
    preflight = registry.load_preflight("podcast")
    assert preflight is not None

    workspace = tmp_path / "podcast"
    workspace.mkdir()
    (workspace / "output.md").write_text("# Test Essay\n\n" + ("Body text. " * 20), encoding="utf-8")

    passed, msg = preflight(workspace, "task131", "make a podcast from the current draft", "ang", "thread1")
    assert passed is True, msg


def test_video_preflight_allows_review_phase_with_saved_state(tmp_path):
    registry = AgentRegistry()
    preflight = registry.load_preflight("video")
    assert preflight is not None

    input_dir = tmp_path / "clips"
    input_dir.mkdir()
    output_dir = tmp_path / "render"
    workspace = tmp_path / "video_task"
    workspace.mkdir()
    (workspace / "video_state.json").write_text(
        json.dumps(
            {
                "phase": "screenplay_review",
                "input_dir": str(input_dir),
                "output_dir": str(output_dir),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    passed, msg = preflight(workspace, "task132", "revise the opening", "ang", "thread1")
    assert passed is True, msg


def test_video_preflight_allows_done_phase_follow_up(tmp_path):
    registry = AgentRegistry()
    preflight = registry.load_preflight("video")
    assert preflight is not None

    workspace = tmp_path / "video_task"
    workspace.mkdir()
    (workspace / "video_state.json").write_text(
        json.dumps({"phase": "done", "output": "/tmp/final.mp4"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    passed, msg = preflight(workspace, "task132b", "再给我看看结果", "ang", "thread1")
    assert passed is True, msg


def test_photo_preflight_requires_inputs_or_style_context(tmp_path):
    registry = AgentRegistry()
    preflight = registry.load_preflight("photo")
    assert preflight is not None

    workspace = tmp_path / "photo_task"
    workspace.mkdir()

    passed, msg = preflight(workspace, "task133", "修图", "ang", "thread1")
    assert passed is False
    assert "PREFLIGHT BLOCKED [photo]" in msg


def test_photo_preflight_blocks_preset_without_style(tmp_path):
    registry = AgentRegistry()
    preflight = registry.load_preflight("photo")
    assert preflight is not None
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setitem(preflight.__globals__, "_load_active_style", lambda: None)

    workspace = tmp_path / "photo_task"
    workspace.mkdir()

    passed, msg = preflight(workspace, "task134", "导出preset", "ang", "thread1")
    assert passed is False
    assert "style profile" in msg
    monkeypatch.undo()


def test_photo_preflight_accepts_reference_dir_for_style_learning(tmp_path):
    registry = AgentRegistry()
    preflight = registry.load_preflight("photo")
    assert preflight is not None

    ref_dir = tmp_path / "refs"
    ref_dir.mkdir()
    (ref_dir / "sample.jpg").write_text("fake image", encoding="utf-8")
    workspace = tmp_path / "photo_task"
    workspace.mkdir()

    passed, msg = preflight(workspace, "task135", f'学习风格 "{ref_dir}"', "ang", "thread1")
    assert passed is True, msg


def test_photo_preflight_allows_style_learning_recovery_with_new_path(tmp_path):
    registry = AgentRegistry()
    preflight = registry.load_preflight("photo")
    assert preflight is not None

    workspace = tmp_path / "photo_task"
    workspace.mkdir()
    (workspace / "photo_state.json").write_text(
        json.dumps(
            {
                "phase": "style_learning",
                "reference_dir": str(tmp_path / "missing_refs"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    new_ref_dir = tmp_path / "fresh_refs"
    new_ref_dir.mkdir()
    (new_ref_dir / "sample.jpg").write_text("fake image", encoding="utf-8")

    passed, msg = preflight(workspace, "task136", f'学习风格 "{new_ref_dir}"', "ang", "thread1")
    assert passed is True, msg


def test_secret_preflight_blocks_missing_explicit_file_reference(tmp_path):
    registry = AgentRegistry()
    preflight = registry.load_preflight("secret")
    assert preflight is not None

    workspace = tmp_path / "secret_task"
    workspace.mkdir()

    passed, msg = preflight(workspace, "task137", "@file:/definitely/missing/file.pdf 帮我总结", "ang", "thread1")
    assert passed is False
    assert "PREFLIGHT BLOCKED [secret]" in msg


def test_secret_preflight_allows_plain_private_question(tmp_path):
    registry = AgentRegistry()
    preflight = registry.load_preflight("secret")
    assert preflight is not None

    workspace = tmp_path / "secret_task"
    workspace.mkdir()

    passed, msg = preflight(workspace, "task138", "帮我算一下今年的税务影响", "ang", "thread1")
    assert passed is True, msg


def test_health_preflight_blocks_missing_checkup_dir(tmp_path):
    registry = AgentRegistry()
    preflight = registry.load_preflight("health")
    assert preflight is not None

    workspace = tmp_path / "task"
    workspace.mkdir()

    passed, msg = preflight(
        workspace,
        "task139",
        "体检报告上传\n路径: users/ang/health/checkups/missing",
        "ang",
        "thread1",
    )
    assert passed is False
    assert "PREFLIGHT BLOCKED [health]" in msg


def test_health_preflight_accepts_existing_checkup_dir(tmp_path, monkeypatch):
    registry = AgentRegistry()
    preflight = registry.load_preflight("health")
    assert preflight is not None

    bridge = tmp_path / "bridge"
    checkup_dir = bridge / "users" / "ang" / "health" / "checkups" / "2026-04-05"
    checkup_dir.mkdir(parents=True)
    (checkup_dir / "report.jpg").write_text("fake image", encoding="utf-8")
    workspace = tmp_path / "task"
    workspace.mkdir()
    monkeypatch.setenv("MIRA_DIR", str(bridge))

    passed, msg = preflight(
        workspace,
        "task140",
        "体检报告上传\n路径: users/ang/health/checkups/2026-04-05",
        "ang",
        "thread1",
    )
    assert passed is True, msg


def test_legacy_publish_uses_registry_preflight_and_preserves_needs_input(tmp_path, monkeypatch):
    import handlers_legacy

    workspace = tmp_path / "task"
    workspace.mkdir()

    class FakeRegistry:
        def load_preflight(self, name: str):
            assert name == "socialmedia"
            return lambda ws, task_id, instruction, sender, thread_id, **kwargs: (True, "")

        def load_handler(self, name: str):
            assert name == "socialmedia"

            def handler(ws, task_id, instruction, sender, thread_id, **kwargs):
                (ws / "output.md").write_text("Confirm publish?", encoding="utf-8")
                return "NEEDS_APPROVAL:Confirm publish?"

            return handler

    monkeypatch.setattr("handlers_legacy.get_registry", lambda: FakeRegistry())
    monkeypatch.setattr("handlers_legacy._update_thread_memory", lambda *args, **kwargs: None)
    _patch_task_worker_test_side_effects(monkeypatch)

    handlers_legacy._handle_publish(workspace, "task141", "publish this", "ang", "thread1")

    result = json.loads((workspace / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "needs-input"
    assert result["summary"] == "Confirm publish?"
    assert result["agent"] == "socialmedia"


def test_legacy_secret_respects_registry_preflight_block(tmp_path, monkeypatch):
    import handlers_legacy

    workspace = tmp_path / "task"
    workspace.mkdir()

    class FakeRegistry:
        def load_preflight(self, name: str):
            assert name == "secret"
            return lambda ws, task_id, instruction, sender, thread_id, **kwargs: (
                False,
                "PREFLIGHT BLOCKED [secret]: missing file",
            )

        def load_handler(self, name: str):
            raise AssertionError("secret handler should not run when preflight blocks")

    monkeypatch.setattr("handlers_legacy.get_registry", lambda: FakeRegistry())
    monkeypatch.setattr("handlers_legacy._update_thread_memory", lambda *args, **kwargs: None)
    _patch_task_worker_test_side_effects(monkeypatch)

    handlers_legacy._handle_secret(workspace, "task142", "@file:/missing.pdf 帮我看", "ang", "thread1")

    result = json.loads((workspace / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert "PREFLIGHT BLOCKED [secret]" in result["summary"]
    assert result["agent"] == "secret"
    assert result["tags"] == ["private"]
    assert not (workspace / "output.md").exists()


def test_autowrite_approval_prefers_metadata_file(tmp_path, monkeypatch):
    import handlers_legacy

    workspace = tmp_path / "task"
    workspace.mkdir()
    final_file = tmp_path / "project" / "final.md"
    final_file.parent.mkdir()
    final_file.write_text("# Title\n\nBody", encoding="utf-8")
    meta = {
        "title": "Title",
        "slug": "title-slug",
        "workspace": str(final_file.parent),
        "final_md": str(final_file),
        "auto_podcast": True,
    }
    (workspace / "autowrite_meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    captured = {}

    def fake_update_manifest(slug, **fields):
        captured["slug"] = slug
        captured["fields"] = fields
        return {}

    monkeypatch.setattr("publish.manifest.update_manifest", fake_update_manifest)
    # _write_result transitively triggers auto_flush -> rebuild_memory_index over the
    # whole soul/, plus _extract_knowledge_writeback -> claude_think. Use the same
    # helper the other runtime-contract tests use to neutralize these side effects.
    _patch_task_worker_test_side_effects(monkeypatch)
    monkeypatch.setattr("task_worker._extract_knowledge_writeback", lambda *a, **k: None)
    handlers_legacy._handle_autowrite_approval(workspace, "autowrite_2026-04-05")

    result = json.loads((workspace / "result.json").read_text(encoding="utf-8"))
    assert captured["slug"] == "title-slug"
    assert captured["fields"]["status"] == "approved"
    assert result["status"] == "done"
