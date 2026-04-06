"""Smoke tests — verify core modules import and basic functions work."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def test_dispatch_scheduled_jobs_uses_registry(monkeypatch):
    import core

    jobs = [
        SimpleNamespace(name="explore", inline=False, priority=5),
        SimpleNamespace(name="substack-growth", inline=False, priority=10),
        SimpleNamespace(name="skill-study", inline=False, priority=20),
    ]
    payloads = {
        "explore": {"label": "arxiv_hf", "sources": ["arxiv", "huggingface"]},
        "substack-growth": True,
        "skill-study": {"domain": "video", "group_idx": 2},
    }
    dispatched = []
    session_new = []

    monkeypatch.setattr(core, "get_jobs", lambda: jobs)
    monkeypatch.setattr(core, "evaluate_job_payload", lambda job, **kw: payloads.get(job.name))
    monkeypatch.setattr(
        core,
        "build_job_dispatch",
        lambda job, payload, python_executable, core_path, **kw: {
            "explore": ("explore-arxiv_hf", ["python", "core.py", "explore", "--sources", "arxiv,huggingface", "--slot", "arxiv_hf"]),
            "substack-growth": ("substack-growth", ["python", "core.py", "growth-cycle"]),
            "skill-study": ("skill-study-video", ["python", "core.py", "skill-study", "--group", "2"]),
        }[job.name],
    )
    monkeypatch.setattr(
        core,
        "build_job_session_record",
        lambda job, payload: {
            "explore": {"action": "explore", "detail": "arxiv_hf"},
            "substack-growth": {"action": "growth_cycle", "detail": ""},
            "skill-study": None,
        }[job.name],
    )
    monkeypatch.setattr(core, "_dispatch_background", lambda name, cmd: dispatched.append((name, cmd)))

    core._dispatch_scheduled_jobs(session_new)

    assert [name for name, _ in dispatched] == [
        "explore-arxiv_hf",
        "substack-growth",
        "skill-study-video",
    ]
    assert "--sources" in dispatched[0][1]
    assert "arxiv,huggingface" in dispatched[0][1]
    assert dispatched[1][1][-1] == "growth-cycle"
    assert dispatched[2][1][-2:] == ["--group", "2"]
    assert [entry["action"] for entry in session_new] == ["explore", "growth_cycle"]
    assert session_new[0]["detail"] == "arxiv_hf"


def test_dispatch_scheduled_jobs_runs_inline_jobs(monkeypatch):
    import core

    jobs = [
        SimpleNamespace(name="health-check", inline=True, inline_runner="health-check", priority=1),
        SimpleNamespace(name="log-cleanup", inline=True, inline_runner="log-cleanup", priority=2),
    ]
    ran = []

    monkeypatch.setattr(core, "get_jobs", lambda: jobs)
    monkeypatch.setattr(core, "evaluate_job_payload", lambda job, **kw: True)
    monkeypatch.setattr(core, "_run_inline_scheduled_job", lambda job, payload: ran.append(job.name))

    core._dispatch_scheduled_jobs([])

    assert ran == ["health-check", "log-cleanup"]


def test_load_state_user_namespace_round_trip(monkeypatch, tmp_path):
    import core

    state_file = tmp_path / ".agent_state.json"
    monkeypatch.setattr(core, "STATE_FILE", state_file)

    core.save_state({"global_flag": True})
    core.save_state({"last_spark_check": "2026-04-05T00:00:00"}, user_id="liquan")

    assert core.load_state() == {
        "global_flag": True,
        "users": {"liquan": {"last_spark_check": "2026-04-05T00:00:00"}},
    }
    assert core.load_state(user_id="liquan") == {"last_spark_check": "2026-04-05T00:00:00"}


def test_load_state_user_namespace_falls_back_to_legacy_flat_keys(monkeypatch, tmp_path):
    import core

    state_file = tmp_path / ".agent_state.json"
    monkeypatch.setattr(core, "STATE_FILE", state_file)
    state_file.write_text(json.dumps({
        "journal_2026-04-05": "done",
        "last_reflect": "2026-04-05T01:00:00",
        "spontaneous_idea_2026-04-05": "title",
        "global_flag": True,
    }), encoding="utf-8")

    assert core.load_state(user_id="ang") == {
        "journal_2026-04-05": "done",
        "last_reflect": "2026-04-05T01:00:00",
        "spontaneous_idea_2026-04-05": "title",
    }
    assert core.load_state(user_id="liquan") == {}


def test_save_state_user_namespace_preserves_existing_users(monkeypatch, tmp_path):
    import core

    state_file = tmp_path / ".agent_state.json"
    monkeypatch.setattr(core, "STATE_FILE", state_file)
    state_file.write_text(json.dumps({
        "users": {
            "ang": {"last_spark_check": "2026-04-05T00:00:00"},
        }
    }), encoding="utf-8")

    core.save_state({"last_reflect": "2026-04-05T01:00:00"}, user_id="liquan")

    merged = json.loads(state_file.read_text(encoding="utf-8"))
    assert merged["users"]["ang"]["last_spark_check"] == "2026-04-05T00:00:00"
    assert merged["users"]["liquan"]["last_reflect"] == "2026-04-05T01:00:00"


def test_dispatch_scheduled_jobs_dispatches_per_user_jobs(monkeypatch):
    import core

    jobs = [
        SimpleNamespace(name="idle-think", inline=False, priority=5, per_user=True),
    ]
    dispatched = []
    session_new = []

    monkeypatch.setattr(core, "get_jobs", lambda: jobs)
    monkeypatch.setattr(core, "get_known_user_ids", lambda: ["ang", "liquan"])
    monkeypatch.setattr(
        core,
        "evaluate_job_payload",
        lambda job, user_id=None: True if user_id == "liquan" else None,
    )
    monkeypatch.setattr(
        core,
        "build_job_dispatch",
        lambda job, payload, python_executable, core_path, user_id=None: (
            f"idle-think-{user_id}",
            ["python", "core.py", "idle-think", "--user", user_id],
        ),
    )
    monkeypatch.setattr(
        core,
        "build_job_session_record",
        lambda job, payload: {"action": "idle_think", "detail": ""},
    )
    monkeypatch.setattr(core, "_dispatch_background", lambda name, cmd: dispatched.append((name, cmd)))

    core._dispatch_scheduled_jobs(session_new)

    assert dispatched == [("idle-think-liquan", ["python", "core.py", "idle-think", "--user", "liquan"])]
    assert len(session_new) == 1
    assert session_new[0]["action"] == "idle_think"
    assert session_new[0]["detail"] == "liquan"
    assert "ts" in session_new[0]


def test_dispatch_scheduled_jobs_records_state_only_after_success(monkeypatch):
    import core

    job = SimpleNamespace(
        name="backlog-executor",
        inline=False,
        priority=5,
        per_user=False,
        state_key=lambda today="", slot="": "last_backlog_executor",
    )
    saved = []

    monkeypatch.setattr(core, "get_jobs", lambda: [job])
    monkeypatch.setattr(core, "evaluate_job_payload", lambda job, **kw: True)
    monkeypatch.setattr(
        core,
        "build_job_dispatch",
        lambda job, payload, python_executable, core_path, **kw: ("backlog-executor", ["python", "core.py", "backlog-executor"]),
    )
    monkeypatch.setattr(core, "_dispatch_background", lambda name, cmd: True)
    monkeypatch.setattr(core, "build_job_session_record", lambda job, payload: None)
    monkeypatch.setattr(core, "load_state", lambda user_id=None: {})
    monkeypatch.setattr(core, "save_state", lambda state, user_id=None: saved.append((state, user_id)))

    core._dispatch_scheduled_jobs([])

    assert len(saved) == 1
    assert "last_backlog_executor" in saved[0][0]


def test_dispatch_scheduled_jobs_does_not_record_state_when_dispatch_fails(monkeypatch):
    import core

    job = SimpleNamespace(
        name="restore-dry-run",
        inline=False,
        priority=5,
        per_user=False,
        state_key=lambda today="", slot="": f"restore_dry_run_{today}",
    )
    saved = []

    monkeypatch.setattr(core, "get_jobs", lambda: [job])
    monkeypatch.setattr(core, "evaluate_job_payload", lambda job, **kw: True)
    monkeypatch.setattr(
        core,
        "build_job_dispatch",
        lambda job, payload, python_executable, core_path, **kw: ("restore-dry-run", ["python", "core.py", "restore-dry-run"]),
    )
    monkeypatch.setattr(core, "_dispatch_background", lambda name, cmd: False)
    monkeypatch.setattr(core, "build_job_session_record", lambda job, payload: None)
    monkeypatch.setattr(core, "load_state", lambda user_id=None: {})
    monkeypatch.setattr(core, "save_state", lambda state, user_id=None: saved.append((state, user_id)))

    core._dispatch_scheduled_jobs([])

    assert saved == []


def test_do_talk_routes_completed_task_to_matching_user_bridge(monkeypatch):
    import core
    if core.Mira is None:
        pytest.skip("mira_bridge not available (CI)")

    class FakeBridge:
        def __init__(self, user_id):
            self.user_id = user_id
            self.updated = []
            self.heartbeats = []

        def heartbeat(self, agent_status):
            self.heartbeats.append(agent_status)

        def update_status(self, task_id, status, agent_message="", error=None):
            self.updated.append((task_id, status, agent_message, error))

        def set_tags(self, task_id, tags):
            pass

        def add_followup(self, todo_id, content, source="agent"):
            pass

        def update_todo(self, todo_id, status="done"):
            pass

        def poll_commands(self):
            return []

        def poll(self):
            return []

        def cleanup_old(self, days=0):
            pass

        def get_next_todo(self):
            return None

        def items_dir(self):
            return None

    ang = FakeBridge("ang")
    liquan = FakeBridge("liquan")

    rec = SimpleNamespace(
        task_id="req_1",
        user_id="liquan",
        status="done",
        summary="done",
        tags=[],
        workspace="",
    )

    class FakeTaskManager:
        def get_status_summary(self):
            return {"busy": False, "active_count": 0, "active_tasks": [], "last_completed": ""}

        def check_tasks(self):
            return [rec]

        def get_reply_content(self, completed):
            return "reply body"

        def get_active_count(self):
            return 0

        def cleanup_old_records(self, max_age_days=7):
            pass

    monkeypatch.setattr(core.Mira, "for_all_users", classmethod(lambda cls: [ang, liquan]))
    monkeypatch.setattr(core, "TaskManager", FakeTaskManager)
    monkeypatch.setattr(core, "_status_footer", lambda task_mgr: "")
    monkeypatch.setattr(core, "_sweep_stuck_items", lambda bridge, task_mgr: None)

    core.do_talk()

    assert not ang.updated
    assert liquan.updated == [("req_1", "done", "reply body", None)]


def test_do_talk_stops_other_legacy_inboxes_when_busy(monkeypatch):
    import core
    import emptiness
    if core.Mira is None:
        pytest.skip("mira_bridge not available (CI)")

    class FakeMessage:
        def __init__(self, msg_id, content, user_id):
            self.id = msg_id
            self.thread_id = ""
            self.sender = "user"
            self.content = content
            self.user_id = user_id

    class FakeBridge:
        def __init__(self, user_id, polled):
            self.user_id = user_id
            self._polled = polled
            self.processed = []
            self.poll_calls = 0

        def heartbeat(self, agent_status):
            pass

        def poll_commands(self):
            return []

        def poll(self):
            self.poll_calls += 1
            return self._polled

        def task_exists(self, task_id):
            return False

        def ack(self, msg_id, status="received"):
            pass

        def create_task(self, task_id, title, first_message, sender="user", tags=None, origin="user"):
            pass

        def append_task_message(self, task_id, sender, content):
            pass

        def update_task_status(self, task_id, status, agent_message=""):
            pass

        def reply(self, msg_id, recipient, content, thread_id=""):
            pass

        def mark_processed(self, msg_path):
            self.processed.append(msg_path)

        def cleanup_old(self, days=0):
            pass

        def get_next_todo(self):
            return None

    ang = FakeBridge("ang", [(FakeMessage("m1", "first", "ang"), "ang-msg")])
    liquan = FakeBridge("liquan", [(FakeMessage("m2", "second", "liquan"), "liquan-msg")])

    class FakeTaskManager:
        def get_status_summary(self):
            return {"busy": False, "active_count": 0, "active_tasks": [], "last_completed": ""}

        def check_tasks(self):
            return []

        def get_active_count(self):
            return 0

        def cleanup_old_records(self, max_age_days=7):
            pass

        def is_dispatched(self, msg_id):
            return False

        def find_failed_task(self, task_id):
            return None

        def dispatch(self, msg, workspace):
            return ""

        def is_busy(self):
            return True

    monkeypatch.setattr(core.Mira, "for_all_users", classmethod(lambda cls: [ang, liquan]))
    monkeypatch.setattr(core, "TaskManager", FakeTaskManager)
    monkeypatch.setattr(core, "_sweep_stuck_items", lambda bridge, task_mgr: None)
    monkeypatch.setattr(core, "_is_meta_command", lambda content: False)
    monkeypatch.setattr(core, "_talk_slug", lambda content, task_id: task_id)
    external_inputs = []
    monkeypatch.setattr(emptiness, "on_external_input", lambda user_id: external_inputs.append(user_id))

    core.do_talk()

    assert ang.processed == []
    assert liquan.processed == []
    assert ang.poll_calls == 1
    assert liquan.poll_calls == 0
    assert external_inputs == ["ang"]


def test_do_talk_stops_retry_when_retry_ceiling_reached(monkeypatch):
    import core
    import emptiness
    if core.Mira is None:
        pytest.skip("mira_bridge not available (CI)")

    class FakeMessage:
        def __init__(self):
            self.id = "followup_1"
            self.thread_id = "req_old"
            self.sender = "user"
            self.content = "retry please"
            self.user_id = "ang"

    class FakeBridge:
        def __init__(self):
            self.user_id = "ang"
            self.replies = []
            self.status_updates = []
            self.processed = []

        def heartbeat(self, agent_status):
            pass

        def poll_commands(self):
            return []

        def poll(self):
            return [(FakeMessage(), "legacy-msg")]

        def task_exists(self, task_id):
            return True

        def ack(self, msg_id, status="received"):
            pass

        def create_task(self, task_id, title, first_message, sender="user", tags=None, origin="user"):
            pass

        def append_task_message(self, task_id, sender, content):
            pass

        def update_task_status(self, task_id, status, agent_message=""):
            self.status_updates.append((task_id, status, agent_message))

        def reply(self, msg_id, recipient, content, thread_id=""):
            self.replies.append((msg_id, recipient, content, thread_id))

        def mark_processed(self, msg_path):
            self.processed.append(msg_path)

        def cleanup_old(self, days=0):
            pass

        def get_next_todo(self):
            return None

    failed_rec = SimpleNamespace(
        task_id="req_old",
        workspace="",
        attempt_count=2,
        max_attempts=2,
        status="failed",
    )

    class FakeTaskManager:
        def get_status_summary(self):
            return {"busy": False, "active_count": 0, "active_tasks": [], "last_completed": ""}

        def check_tasks(self):
            return []

        def get_active_count(self):
            return 0

        def cleanup_old_records(self, max_age_days=7):
            pass

        def is_dispatched(self, msg_id):
            return False

        def find_failed_task(self, task_id):
            assert task_id == "req_old"
            return failed_rec

        def can_retry(self, rec):
            return False

        def reset_for_retry(self, task_id):
            raise AssertionError("should not reset when retry ceiling is reached")

        def dispatch(self, msg, workspace, **kwargs):
            raise AssertionError("should not dispatch when retry ceiling is reached")

        def is_busy(self):
            return False

    bridge = FakeBridge()
    monkeypatch.setattr(core.Mira, "for_all_users", classmethod(lambda cls: [bridge]))
    monkeypatch.setattr(core, "TaskManager", FakeTaskManager)
    monkeypatch.setattr(core, "_sweep_stuck_items", lambda bridge, task_mgr: None)
    monkeypatch.setattr(core, "_is_meta_command", lambda content: False)
    monkeypatch.setattr(core, "_talk_slug", lambda content, task_id: task_id)
    monkeypatch.setattr(emptiness, "on_external_input", lambda user_id: None)

    core.do_talk()

    assert bridge.processed == ["legacy-msg"]
    assert bridge.status_updates == [("req_old", "failed", "")]
    assert len(bridge.replies) == 1
    assert "重试上限" in bridge.replies[0][2]
    assert bridge.replies[0][3] == "req_old"


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


def test_writing_agent_cmd_run_delegates_to_canonical_pipeline(monkeypatch):
    import writing_agent

    monkeypatch.setattr(writing_agent, "_run_canonical_pipeline", lambda: 7)

    assert writing_agent.cmd_run() == 7


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


def test_writing_agent_cmd_auto_delegates_to_canonical_runner(monkeypatch):
    import writing_agent

    captured = {}
    monkeypatch.setattr(
        writing_agent,
        "_run_canonical_autowrite",
        lambda title, writing_type, idea_content: captured.update({
            "title": title,
            "writing_type": writing_type,
            "idea_content": idea_content,
        }),
    )

    writing_agent.cmd_auto("Title", "essay", "Idea body")

    assert captured == {
        "title": "Title",
        "writing_type": "essay",
        "idea_content": "Idea body",
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


def test_writing_agent_iterate_falls_back_to_legacy(monkeypatch, tmp_path, capsys):
    import writing_agent

    monkeypatch.setattr(writing_agent, "_find_canonical_project", lambda slug: None)
    monkeypatch.setattr(writing_agent, "IDEAS_DIR", tmp_path / "ideas")
    writing_agent.IDEAS_DIR.mkdir()
    (writing_agent.IDEAS_DIR / "legacy.md").write_text(
        "# Legacy\n\n---\n<!-- AUTO-MANAGED BELOW -->\n## Status\n\n- **state**: scaffolded\n- **project_dir**: \n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        writing_agent,
        "parse_idea",
        lambda path: {"slug": "legacy", "state": "drafting", "project_dir": "", "path": path},
    )
    monkeypatch.setattr(writing_agent, "advance_idea", lambda idea: True)

    writing_agent.cmd_iterate("legacy")

    output = capsys.readouterr().out
    assert "falling back to legacy idea files" in output
    assert "[legacy] Current state: drafting" in output
    assert "[legacy] Advanced to: drafting" in output


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
