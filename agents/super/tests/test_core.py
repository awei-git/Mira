"""Smoke tests — verify core modules import and basic functions work."""
from __future__ import annotations
import json
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


def test_canonical_writing_pipeline_only_advances_plan_ready(monkeypatch, tmp_path):
    import core

    advanced = []
    workspace_a = tmp_path / "a"
    workspace_b = tmp_path / "b"
    workspace_a.mkdir()
    workspace_b.mkdir()

    monkeypatch.setattr(core, "check_writing_responses", lambda: [
        {"workspace": workspace_a, "project": {"title": "Plan", "phase": "plan_ready"}},
        {"workspace": workspace_b, "project": {"title": "Draft", "phase": "draft_ready"}},
    ])
    monkeypatch.setattr(core, "advance_project", lambda workspace: advanced.append(workspace))

    count = core._run_canonical_writing_pipeline()

    assert count == 1
    assert advanced == [workspace_a]


def test_run_autowrite_pipeline_writes_metadata_and_requests_approval(monkeypatch, tmp_path):
    from workflows import writing

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    final_file = project_dir / "final.md"
    final_file.write_text("# Test Essay\n\nBody text.", encoding="utf-8")

    class FakeBridge:
        def __init__(self):
            self.calls = []

        def update_task_status(self, task_id, status, agent_message=""):
            self.calls.append((task_id, status, agent_message))

    bridge = FakeBridge()
    monkeypatch.setattr(writing, "_TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(writing, "Mira", lambda: bridge)
    monkeypatch.setattr(writing, "run_full_pipeline", lambda title, body: (project_dir, final_file.read_text(encoding="utf-8")))

    writing.run_autowrite_pipeline("autowrite_2026-04-05", "Test Essay", "essay", "idea body")

    task_ws = (tmp_path / "tasks" / "autowrite_2026-04-05")
    meta = json.loads((task_ws / "autowrite_meta.json").read_text(encoding="utf-8"))
    assert meta["slug"] == project_dir.name
    assert meta["final_md"] == str(final_file)
    assert bridge.calls
    assert bridge.calls[-1][1] == "needs-input"


def test_writing_agent_run_command_uses_canonical_pipeline(monkeypatch, tmp_path):
    import writing_agent

    workspace = tmp_path / "project"
    workspace.mkdir()
    advanced = []

    monkeypatch.setattr(
        writing_agent,
        "_get_canonical_writing_ops",
        lambda: (
            lambda: [{"workspace": workspace, "project": {"phase": "plan_ready"}}],
            lambda path: advanced.append(path),
        ),
    )

    count = writing_agent._run_canonical_pipeline()

    assert count == 1
    assert advanced == [workspace]


def test_writing_agent_auto_command_uses_canonical_runner(monkeypatch):
    import writing_agent

    captured = {}
    monkeypatch.setattr(
        writing_agent,
        "_get_canonical_autowrite_runner",
        lambda: lambda task_id, title, writing_type, idea: captured.update({
            "task_id": task_id,
            "title": title,
            "writing_type": writing_type,
            "idea": idea,
        }),
    )

    writing_agent._run_canonical_autowrite("Title", "essay", "Idea body", task_id="autowrite_test")

    assert captured == {
        "task_id": "autowrite_test",
        "title": "Title",
        "writing_type": "essay",
        "idea": "Idea body",
    }


def test_writing_agent_iterate_prefers_canonical_project(monkeypatch, tmp_path, capsys):
    import writing_agent

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    advanced = []
    state = {"phase": "plan_ready"}

    monkeypatch.setattr(
        writing_agent,
        "_find_canonical_project",
        lambda slug: (project_dir, {"phase": state["phase"]}) if slug == "proj" else None,
    )
    monkeypatch.setattr(
        writing_agent,
        "_get_canonical_writing_ops",
        lambda: (None, lambda workspace: advanced.append(workspace)),
    )
    monkeypatch.setattr(
        writing_agent,
        "_iter_canonical_projects",
        lambda: [(project_dir, {"phase": "draft_ready"})],
    )

    writing_agent.cmd_iterate("proj")

    output = capsys.readouterr().out
    assert "Canonical project proj: phase=plan_ready" in output
    assert "Advanced to: draft_ready" in output
    assert advanced == [project_dir]


def test_writing_agent_status_lists_canonical_and_legacy(monkeypatch, tmp_path, capsys):
    import writing_agent

    monkeypatch.setattr(
        writing_agent,
        "_iter_canonical_projects",
        lambda: [(tmp_path / "proj", {"phase": "draft_ready", "version": 2, "updated_at": "2026-04-05T12:00:00"})],
    )
    monkeypatch.setattr(writing_agent, "IDEAS_DIR", tmp_path / "ideas")
    writing_agent.IDEAS_DIR.mkdir()
    (writing_agent.IDEAS_DIR / "legacy.md").write_text(
        "# Legacy\n\n---\n<!-- AUTO-MANAGED BELOW -->\n## Status\n\n- **state**: new\n",
        encoding="utf-8",
    )

    writing_agent.cmd_status()

    output = capsys.readouterr().out
    assert "proj" in output
    assert "draft_ready" in output
    assert "Legacy idea files still present:" in output
    assert "legacy: new" in output
