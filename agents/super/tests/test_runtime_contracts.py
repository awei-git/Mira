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


def test_execute_plan_steps_rewrites_done_without_output_to_error(tmp_path, monkeypatch):
    """A handler cannot claim done without producing a verifiable artifact."""
    workspace = tmp_path / "task"
    workspace.mkdir()

    class FakeRegistry:
        def load_handler(self, name: str):
            assert name == "general"

            def handler(ws, task_id, instruction, sender, thread_id):
                (ws / "result.json").write_text(
                    json.dumps({
                        "task_id": task_id,
                        "status": "done",
                        "summary": "Claimed success",
                    }, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return "Claimed success"

            return handler

    monkeypatch.setattr("agent_registry.get_registry", lambda: FakeRegistry())
    _patch_task_worker_test_side_effects(monkeypatch)

    plan = [{
        "agent": "general",
        "instruction": "Do something",
        "tier": "light",
        "prediction": {"difficulty": "easy", "failure_modes": [], "success_criteria": "done"},
    }]
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
    assert result["status"] == "error"
    assert "no verifiable output" in result["summary"]


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


def test_execute_plan_steps_respects_registry_preflight(tmp_path, monkeypatch):
    """Registry preflight can block execution before the handler runs."""
    workspace = tmp_path / "task"
    workspace.mkdir()
    called = {"handler": False}

    class FakeRegistry:
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

    plan = [{
        "agent": "writer",
        "instruction": "",
        "tier": "light",
        "prediction": {"difficulty": "easy", "failure_modes": [], "success_criteria": "blocked"},
    }]
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
    assert result["status"] == "error"
    assert "PREFLIGHT BLOCKED" in result["summary"]
    assert called["handler"] is False


def test_execute_plan_steps_fails_closed_on_preflight_exception(tmp_path, monkeypatch):
    """Preflight exceptions must fail closed, not fall back to general."""
    workspace = tmp_path / "task"
    workspace.mkdir()
    called = {"handler": False}

    class FakeRegistry:
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

    plan = [{
        "agent": "writer",
        "instruction": "write it",
        "tier": "light",
        "prediction": {"difficulty": "easy", "failure_modes": [], "success_criteria": "blocked"},
    }]
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
    assert result["status"] == "error"
    assert "preflight failed" in result["summary"]
    assert called["handler"] is False


def test_execute_plan_steps_falls_back_when_preflight_load_errors(tmp_path, monkeypatch):
    """Registry preflight import/load errors should follow normal general fallback."""
    workspace = tmp_path / "task"
    workspace.mkdir()
    called = {"general": False, "handler": False}

    class FakeRegistry:
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
    monkeypatch.setattr(task_worker, "_handle_general", fake_general)
    _patch_task_worker_test_side_effects(monkeypatch)

    plan = [{
        "agent": "writer",
        "instruction": "write it",
        "tier": "light",
        "prediction": {"difficulty": "easy", "failure_modes": [], "success_criteria": "fallback"},
    }]
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
    assert result.startswith("NEEDS_APPROVAL:")


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

    passed, msg = preflight(workspace, "task135", f"学习风格 \"{ref_dir}\"", "ang", "thread1")
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

    passed, msg = preflight(workspace, "task136", f"学习风格 \"{new_ref_dir}\"", "ang", "thread1")
    assert passed is True, msg


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

    monkeypatch.setattr("publish_manifest.update_manifest", fake_update_manifest)
    handlers_legacy._handle_autowrite_approval(workspace, "autowrite_2026-04-05")

    result = json.loads((workspace / "result.json").read_text(encoding="utf-8"))
    assert captured["slug"] == "title-slug"
    assert captured["fields"]["status"] == "approved"
    assert result["status"] == "done"
