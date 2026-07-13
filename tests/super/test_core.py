"""Smoke tests — verify core modules import and basic functions work."""

from __future__ import annotations
import ast
import importlib.util
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

# 2026-04-28: tests monkeypatch symbols looked up in jobs.py / state.py;
# previously patched on `core` after a refactor moved them out.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "agents" / "super"))
import jobs  # noqa: E402
import state  # noqa: E402
import talk  # noqa: E402

# `writing` requires agents/writer on path (writing_workflow lives there).
# tests/super/conftest.py adds it; for safety also add here.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "agents" / "writer"))
import writing  # noqa: E402


def test_core_imports():
    import core

    assert hasattr(core, "cmd_run"), "core.py missing cmd_run"


def test_canonical_config_exports_imported_symbols():
    repo_root = Path(__file__).resolve().parent.parent.parent
    config_path = repo_root / "lib" / "config.py"
    spec = importlib.util.spec_from_file_location("_mira_test_config", config_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    exported = set(dir(module))

    missing: dict[str, list[str]] = {}
    for path in repo_root.rglob("*.py"):
        if any(part in {".venv", "__pycache__"} for part in path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module != "config":
                continue
            for alias in node.names:
                if alias.name != "*" and alias.name not in exported:
                    missing.setdefault(alias.name, []).append(str(path.relative_to(repo_root)))

    assert not missing


def test_control_plane_dispatch_resets_terminal_local_record(monkeypatch):
    item = {
        "id": "self_audit_20260503",
        "user_id": "default",
        "title": "自检报告",
        "messages": [{"sender": "default", "content": "修改了吗", "timestamp": "2026-05-03T20:05:07Z"}],
    }
    calls = []

    class FakeRepo:
        def __init__(self, conn):
            self.conn = conn

        def list_dispatchable_tasks(self, limit):
            return [item]

        def claim_task_for_dispatch(self, user_id, task_id):
            calls.append(("claim", user_id, task_id))
            return True

        def release_dispatch_claim(self, user_id, task_id, *, reason):
            calls.append(("release", user_id, task_id, reason))

    @contextmanager
    def fake_transaction():
        yield object()

    @contextmanager
    def fake_advisory_lock(*args, **kwargs):
        yield

    class FakeTaskManager:
        def is_busy(self):
            return False

        def get_active_count(self):
            return 0

        def is_dispatched(self, task_id):
            return True

        def find_failed_task(self, task_id):
            return SimpleNamespace(task_id=task_id, status="completed_unverified")

        def reset_for_retry(self, task_id):
            calls.append(("reset", task_id))

        def dispatch(self, msg, workspace):
            calls.append(("dispatch", msg.id, msg.content))
            return msg.id

    import control.db
    import control.repository

    monkeypatch.setattr(control.db, "transaction", fake_transaction)
    monkeypatch.setattr(control.repository, "ControlRepository", FakeRepo)
    monkeypatch.setattr(talk, "advisory_lock", fake_advisory_lock)
    monkeypatch.setattr(talk, "_check_inbound_command_safety", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        talk,
        "get_user_config",
        lambda user_id: {
            "role": "admin",
            "model_restriction": None,
            "content_filter": False,
            "allowed_agents": ["general"],
        },
    )

    talk._dispatch_control_plane_tasks(FakeTaskManager(), {"default": object()}, object())

    assert calls[:3] == [
        ("reset", "self_audit_20260503"),
        ("claim", "default", "self_audit_20260503"),
        ("dispatch", "self_audit_20260503", "修改了吗"),
    ]


def test_control_plane_intent_gate_uses_latest_human_message(monkeypatch):
    item = {
        "id": "req_control",
        "user_id": "default",
        "title": "Offline fallback",
        "type": "request",
        "messages": [
            {"sender": "default", "content": "turn this into a short essay", "timestamp": "2026-07-01T20:00:00Z"},
            {
                "sender": "agent",
                "content": "Received. Claude API connectivity is unavailable right now.",
                "timestamp": "2026-07-01T20:01:00Z",
            },
        ],
    }
    seen = []

    class FakeRepo:
        def __init__(self, conn):
            self.conn = conn

        def list_dispatchable_tasks(self, limit):
            return [item]

        def claim_task_for_dispatch(self, user_id, task_id):
            raise AssertionError("unclear task should not be claimed")

    @contextmanager
    def fake_transaction():
        yield object()

    @contextmanager
    def fake_advisory_lock(*args, **kwargs):
        yield

    class FakeTaskManager:
        def is_busy(self):
            return False

        def get_active_count(self):
            return 0

        def is_dispatched(self, task_id):
            return False

    import control.db
    import control.repository

    def safety(_bridge, _cmd, _item_id, _title, content):
        seen.append(("safety", content))
        return True

    def gate(_bridge, _item_id, task_description, **_kwargs):
        seen.append(("gate", task_description))
        return False

    monkeypatch.setattr(control.db, "transaction", fake_transaction)
    monkeypatch.setattr(control.repository, "ControlRepository", FakeRepo)
    monkeypatch.setattr(talk, "advisory_lock", fake_advisory_lock)
    monkeypatch.setattr(talk, "CONTROL_RUNTIME_DB_ENABLED", True)
    monkeypatch.setattr(talk, "BRIDGE_COMPAT_EXPORT_ENABLED", False)
    monkeypatch.setattr(talk, "detect_sensitive_content", None)
    monkeypatch.setattr(talk, "_check_inbound_command_safety", safety)
    monkeypatch.setattr(talk, "_intent_gate_allows", gate)
    monkeypatch.setattr(
        talk,
        "get_user_config",
        lambda user_id: {
            "role": "admin",
            "model_restriction": None,
            "content_filter": False,
            "allowed_agents": ["general"],
        },
    )

    talk._dispatch_control_plane_tasks(FakeTaskManager(), {"default": object()}, object())

    assert seen == [
        ("safety", "turn this into a short essay"),
        ("gate", "turn this into a short essay"),
    ]


def test_control_plane_discussion_forces_discussion_route(monkeypatch, tmp_path):
    import core

    item = {
        "id": "disc_daily_collab",
        "user_id": "default",
        "title": "Mira",
        "type": "discussion",
        "tags": ["daily-collab", "conversation", "discussion"],
        "messages": [
            {
                "sender": "default",
                "content": "Codex diagnostic ping for the Mira app chat path.",
                "timestamp": "2026-07-09T02:13:53Z",
            }
        ],
    }
    user_cfg = {
        "role": "admin",
        "model_restriction": None,
        "content_filter": False,
        "allowed_agents": ["general"],
    }

    msg = talk._message_from_control_item(item, "default", user_cfg)
    monkeypatch.setattr(core, "try_fast_dispatch", lambda _content: "coding")

    assert core._explicit_task_dispatch_agent(msg) == "discussion"
    assert core._apply_fast_dispatch_plan(msg, tmp_path) is None
    assert not (tmp_path / "pending_plan.json").exists()


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


def test_audit_module_hash_matches_current_soul_manager():
    import hashlib

    from agents.shared.config import AUDIT_MODULE_HASH

    soul_manager_path = Path(__file__).resolve().parent.parent.parent / "lib" / "soul_manager.py"
    actual = hashlib.sha256(soul_manager_path.read_bytes()).hexdigest()

    assert AUDIT_MODULE_HASH == actual


def test_content_integrity_allows_short_conversation_output(monkeypatch, tmp_path, caplog):
    import core

    workspace = tmp_path / "conversation"
    workspace.mkdir()
    (workspace / "dispatch_receipt.json").write_text("{}", encoding="utf-8")
    (workspace / "output.md").write_text("No footer now.", encoding="utf-8")

    record = SimpleNamespace(
        task_id="disc_daily_collab",
        status="completed_unverified",
        completed_at="2026-07-01T03:50:11Z",
        workspace=str(workspace),
        tags=["daily-collab", "conversation", "discussion"],
    )

    class FakeTaskManager:
        _records = [record]

    monkeypatch.setattr(core, "TaskManager", FakeTaskManager)

    with caplog.at_level("WARNING"):
        core._check_recent_completed_task_content_integrity()

    assert "truncated_output" not in caplog.text


def test_main_continues_when_skill_audit_integrity_degraded(monkeypatch, tmp_path):
    import core

    calls = []

    @contextmanager
    def fake_launchagent_lock():
        yield

    class FakeProcessLockActive(Exception):
        pass

    monkeypatch.setattr(sys, "argv", ["core.py", "run"])
    monkeypatch.setattr(core, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(core, "Mira", None)
    monkeypatch.setattr(core, "verify_audit_integrity", lambda: False)
    monkeypatch.setattr(core, "validate_soul_files", lambda: [])
    monkeypatch.setattr(core, "check_rules_integrity", lambda: calls.append("rules"))
    monkeypatch.setattr(core, "validate_config", lambda: True)
    monkeypatch.setattr(core, "verify_agent_deps", lambda: None)
    monkeypatch.setattr(core, "validate_local_model_native_tools", lambda logger=None: None)
    monkeypatch.setattr(core, "_log_skill_depth_advisories", lambda command: None)
    monkeypatch.setattr(core, "cmd_run", lambda: calls.append("cmd_run"))
    monkeypatch.setattr(core.soul_manager, "audit_model_dependency", lambda agents: None)
    monkeypatch.setitem(sys.modules, "agent_registry", SimpleNamespace(get_registry=lambda: object()))
    monkeypatch.setitem(sys.modules, "llm", SimpleNamespace(set_usage_agent=lambda agent: None))
    monkeypatch.setitem(
        sys.modules,
        "locks.process",
        SimpleNamespace(ProcessLockActive=FakeProcessLockActive, launchagent_lock=fake_launchagent_lock),
    )

    core.main()

    assert calls == ["rules", "cmd_run"]
    marker = tmp_path / "skill_audit_integrity.json"
    assert json.loads(marker.read_text(encoding="utf-8"))["status"] == "degraded"


def test_main_exits_nonzero_when_soul_integrity_fails(monkeypatch, tmp_path):
    import core

    @contextmanager
    def fake_launchagent_lock():
        yield

    class FakeProcessLockActive(Exception):
        pass

    monkeypatch.setattr(sys, "argv", ["core.py", "run"])
    monkeypatch.setattr(core, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(core, "Mira", None)
    monkeypatch.setattr(core, "verify_audit_integrity", lambda: True)
    monkeypatch.setattr(core, "validate_soul_files", lambda: [("identity.md", "missing")])
    monkeypatch.setitem(
        sys.modules,
        "locks.process",
        SimpleNamespace(ProcessLockActive=FakeProcessLockActive, launchagent_lock=fake_launchagent_lock),
    )

    with pytest.raises(SystemExit) as exc:
        core.main()

    assert exc.value.code == 78


def test_scheduled_pipeline_blind_spot_suppressed_by_recent_scheduler_success(monkeypatch, tmp_path):
    import core

    now = 1_781_560_000.0
    health_file = tmp_path / "bg_health.json"
    health_file.write_text(
        json.dumps(
            {
                "processes": {
                    "writing-pipeline": {
                        "last_success": datetime.fromtimestamp(now - 900, timezone.utc).isoformat(),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    job = SimpleNamespace(name="writing-pipeline", per_user=False)

    monkeypatch.setattr(core, "get_jobs", lambda: [job])
    monkeypatch.setattr(core, "evaluate_job_payload", lambda job, user_id=None: True)
    monkeypatch.setattr(core.mira_config, "HEALTH_FILE", health_file)

    assert core._scheduled_pipeline_blind_spots(now, {"writer": now - 3 * 3600}) == []


def test_scheduled_pipeline_blind_spot_reports_without_recent_scheduler_success(monkeypatch, tmp_path):
    import core

    now = 1_781_560_000.0
    health_file = tmp_path / "bg_health.json"
    health_file.write_text(json.dumps({"processes": {}}), encoding="utf-8")
    job = SimpleNamespace(name="writing-pipeline", per_user=False)

    monkeypatch.setattr(core, "get_jobs", lambda: [job])
    monkeypatch.setattr(core, "evaluate_job_payload", lambda job, user_id=None: True)
    monkeypatch.setattr(core.mira_config, "HEALTH_FILE", health_file)

    anomalies = core._scheduled_pipeline_blind_spots(now, {"writer": now - 3 * 3600})

    assert anomalies == [
        {
            "job": "writing-pipeline",
            "component": "writer",
            "user_id": None,
            "last_output": now - 3 * 3600,
            "output_gap_seconds": 3 * 3600,
        }
    ]


def test_check_stale_pipelines_reports_writer_stall_despite_scheduler_success(monkeypatch, tmp_path):
    import core

    now = 1_781_560_000.0
    (tmp_path / "writing_pipeline_status.json").write_text(
        json.dumps(
            {
                "checked_at": "2026-07-01T12:00:00",
                "stalled": [
                    {
                        "title": "Stalled Essay",
                        "phase": "reviewing",
                        "age_days": 2,
                        "reason": "reviewing: not advanceable by scheduler",
                    }
                ],
                "phase_counts": {"reviewing": 1},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(core, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(core, "STALE_THRESHOLDS", {"writer": 3600})
    monkeypatch.setattr(core, "_read_last_outputs", lambda: {"writer": now - 7200})
    monkeypatch.setattr(core.time, "time", lambda: now)
    monkeypatch.setattr(core, "_recent_scheduler_success_age", lambda component, now: 900)

    stale = core._check_stale_pipelines()

    assert stale[0]["component"] == "writer"
    assert stale[0]["kind"] == "writing_stalled"
    assert stale[0]["stalled_count"] == 1
    assert stale[0]["projects"][0]["title"] == "Stalled Essay"


def test_resolve_soul_integrity_alert_marks_existing_alert_done(monkeypatch):
    import core

    class FakeMira:
        item = {
            "id": "soul_integrity_failure",
            "type": "alert",
            "status": "failed",
            "pinned": True,
            "tags": ["system", "soul", "integrity", "error"],
            "messages": [],
            "error": {"code": "stuck", "message": "Task lost"},
        }

        def __init__(self, *args, **kwargs):
            pass

        def item_exists(self, item_id):
            return item_id == "soul_integrity_failure"

        def _read_item(self, item_id):
            return self.item

        def update_status(self, item_id, status, agent_message=""):
            self.item["status"] = status
            if agent_message:
                self.item["messages"].append({"sender": "agent", "content": agent_message})

        def _write_item(self, item):
            self.item = item
            FakeMira.item = item

        def _update_manifest(self, item):
            pass

    monkeypatch.setattr(core, "Mira", FakeMira)

    core._resolve_soul_integrity_alert()

    assert FakeMira.item["status"] == "done"
    assert FakeMira.item["error"] is None
    assert FakeMira.item["pinned"] is False
    assert "resolved" in FakeMira.item["tags"]
    assert "error" not in FakeMira.item["tags"]


def test_sweep_stuck_items_ignores_active_alerts(tmp_path):
    item_path = tmp_path / "soul_integrity_failure.json"
    item_path.write_text(
        json.dumps(
            {
                "id": "soul_integrity_failure",
                "type": "alert",
                "status": "working",
                "updated_at": "2000-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    calls = []

    class FakeBridge:
        items_dir = tmp_path

        def update_status(self, *args, **kwargs):
            calls.append((args, kwargs))

    talk._sweep_stuck_items(FakeBridge(), object())

    assert calls == []
    assert json.loads(item_path.read_text(encoding="utf-8"))["status"] == "working"


def test_soul_loads():
    from memory.soul import load_soul

    soul = load_soul()
    assert isinstance(soul, dict), f"load_soul returned {type(soul)}"
    assert "identity" in soul, "Soul missing identity"
    assert "worldview" in soul, "Soul missing worldview"


def test_dispatch_scheduled_jobs_uses_registry(monkeypatch):
    import core

    _job_list = [
        SimpleNamespace(name="explore", inline=False, priority=5, per_user=False, blocking_group="heavy"),
        SimpleNamespace(name="substack-growth", inline=False, priority=10, per_user=False, blocking_group="light"),
        SimpleNamespace(name="skill-study", inline=False, priority=20, per_user=False, blocking_group="heavy"),
    ]
    payloads = {
        "explore": {"label": "arxiv_hf", "sources": ["arxiv", "huggingface"]},
        "substack-growth": True,
        "skill-study": {"domain": "video", "group_idx": 2},
    }
    dispatched = []
    session_new = []

    monkeypatch.setattr("jobs.get_jobs", lambda: _job_list)
    monkeypatch.setattr("jobs.evaluate_job_payload", lambda job, **kw: payloads.get(job.name))
    monkeypatch.setattr(
        "jobs.build_job_dispatch",
        lambda job, payload, python_executable, core_path, **kw: {
            "explore": (
                "explore-arxiv_hf",
                ["python", "core.py", "explore", "--sources", "arxiv,huggingface", "--slot", "arxiv_hf"],
            ),
            "substack-growth": ("substack-growth", ["python", "core.py", "growth-cycle"]),
            "skill-study": ("skill-study-video", ["python", "core.py", "skill-study", "--group", "2"]),
        }[job.name],
    )
    monkeypatch.setattr(
        "jobs.build_job_session_record",
        lambda job, payload: {
            "explore": {"action": "explore", "detail": "arxiv_hf"},
            "substack-growth": {"action": "growth_cycle", "detail": ""},
            "skill-study": None,
        }[job.name],
    )
    monkeypatch.setattr("jobs._dispatch_background", lambda name, cmd, **kw: dispatched.append((name, cmd)))
    monkeypatch.setattr(core, "_count_undelivered_outputs", lambda: 0)
    monkeypatch.setattr(core, "_count_pending_active_tasks", lambda: 0)

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

    _job_list = [
        SimpleNamespace(name="health-check", inline=True, inline_runner="health-check", priority=1),
        SimpleNamespace(name="log-cleanup", inline=True, inline_runner="log-cleanup", priority=2),
    ]
    ran = []

    monkeypatch.setattr("jobs.get_jobs", lambda: _job_list)
    monkeypatch.setattr("jobs.evaluate_job_payload", lambda job, **kw: True)
    monkeypatch.setattr("jobs._run_inline_scheduled_job", lambda job, payload: ran.append(job.name))

    core._dispatch_scheduled_jobs([])

    assert ran == ["health-check", "log-cleanup"]


def test_load_state_user_namespace_round_trip(monkeypatch, tmp_path):
    import core

    state_file = tmp_path / ".agent_state.json"
    monkeypatch.setattr("state.STATE_FILE", state_file)

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
    monkeypatch.setattr("state.STATE_FILE", state_file)
    state_file.write_text(
        json.dumps(
            {
                "journal_2026-04-05": "done",
                "last_reflect": "2026-04-05T01:00:00",
                "spontaneous_idea_2026-04-05": "title",
                "global_flag": True,
            }
        ),
        encoding="utf-8",
    )

    assert core.load_state(user_id="default") == {
        "journal_2026-04-05": "done",
        "last_reflect": "2026-04-05T01:00:00",
        "spontaneous_idea_2026-04-05": "title",
    }
    assert core.load_state(user_id="liquan") == {}


def test_save_state_user_namespace_preserves_existing_users(monkeypatch, tmp_path):
    import core

    state_file = tmp_path / ".agent_state.json"
    monkeypatch.setattr("state.STATE_FILE", state_file)
    state_file.write_text(
        json.dumps(
            {
                "users": {
                    "default": {"last_spark_check": "2026-04-05T00:00:00"},
                }
            }
        ),
        encoding="utf-8",
    )

    core.save_state({"last_reflect": "2026-04-05T01:00:00"}, user_id="liquan")

    merged = json.loads(state_file.read_text(encoding="utf-8"))
    assert merged["users"]["default"]["last_spark_check"] == "2026-04-05T00:00:00"
    assert merged["users"]["liquan"]["last_reflect"] == "2026-04-05T01:00:00"


def test_dispatch_scheduled_jobs_dispatches_per_user_jobs(monkeypatch):
    import core

    _job_list = [
        SimpleNamespace(name="idle-think", inline=False, priority=5, per_user=True, blocking_group="local"),
    ]
    dispatched = []
    session_new = []

    monkeypatch.setattr("jobs.get_jobs", lambda: _job_list)
    monkeypatch.setattr("jobs.get_known_user_ids", lambda: ["default", "liquan"])
    monkeypatch.setattr(
        "jobs.evaluate_job_payload",
        lambda job, user_id=None: True if user_id == "liquan" else None,
    )
    monkeypatch.setattr(
        "jobs.build_job_dispatch",
        lambda job, payload, python_executable, core_path, user_id=None: (
            f"idle-think-{user_id}",
            ["python", "core.py", "idle-think", "--user", user_id],
        ),
    )
    monkeypatch.setattr(
        "jobs.build_job_session_record",
        lambda job, payload: {"action": "idle_think", "detail": ""},
    )
    monkeypatch.setattr("jobs._dispatch_background", lambda name, cmd, **kw: dispatched.append((name, cmd)))

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
        blocking_group="light",
        state_key=lambda today="", slot="": "last_backlog_executor",
    )
    saved = []

    monkeypatch.setattr("jobs.get_jobs", lambda: [job])
    monkeypatch.setattr("jobs.evaluate_job_payload", lambda job, **kw: True)
    monkeypatch.setattr(
        "jobs.build_job_dispatch",
        lambda job, payload, python_executable, core_path, **kw: (
            "backlog-executor",
            ["python", "core.py", "backlog-executor"],
        ),
    )
    monkeypatch.setattr("jobs._dispatch_background", lambda name, cmd, **kw: True)
    monkeypatch.setattr("jobs.build_job_session_record", lambda job, payload: None)
    monkeypatch.setattr("jobs.load_state", lambda user_id=None: {})
    monkeypatch.setattr("jobs.save_state", lambda state, user_id=None: saved.append((state, user_id)))

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
        blocking_group="light",
        state_key=lambda today="", slot="": f"restore_dry_run_{today}",
    )
    saved = []

    monkeypatch.setattr("jobs.get_jobs", lambda: [job])
    monkeypatch.setattr("jobs.evaluate_job_payload", lambda job, **kw: True)
    monkeypatch.setattr(
        "jobs.build_job_dispatch",
        lambda job, payload, python_executable, core_path, **kw: (
            "restore-dry-run",
            ["python", "core.py", "restore-dry-run"],
        ),
    )
    monkeypatch.setattr("jobs._dispatch_background", lambda name, cmd, **kw: False)
    monkeypatch.setattr("jobs.build_job_session_record", lambda job, payload: None)
    monkeypatch.setattr("jobs.load_state", lambda user_id=None: {})
    monkeypatch.setattr("jobs.save_state", lambda state, user_id=None: saved.append((state, user_id)))

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

    ang = FakeBridge("default")
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
    monkeypatch.setattr("talk.TaskManager", FakeTaskManager)
    monkeypatch.setattr("talk._status_footer", lambda task_mgr: "")
    monkeypatch.setattr("talk._sweep_stuck_items", lambda bridge, task_mgr: None)
    monkeypatch.setattr("talk.CONTROL_RUNTIME_DB_ENABLED", False)

    core.do_talk()

    assert not ang.updated
    assert liquan.updated == [("req_1", "done", "reply body", None)]


def test_do_talk_stops_other_legacy_inboxes_when_busy(monkeypatch):
    import core
    import evaluation.emptiness as emptiness

    if core.Mira is None:
        pytest.skip("mira_bridge not available (CI)")

    class FakeMessage:
        def __init__(self, msg_id, content, user_id):
            self.id = msg_id
            self.thread_id = ""
            self.sender = "user"
            self.content = content
            self.user_id = user_id

        def to_dict(self):
            return {
                "id": self.id,
                "thread_id": self.thread_id,
                "sender": self.sender,
                "content": self.content,
                "user_id": self.user_id,
            }

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

    ang = FakeBridge("default", [(FakeMessage("m1", "first", "default"), "ang-msg")])
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
    monkeypatch.setattr("talk.TaskManager", FakeTaskManager)
    monkeypatch.setattr("talk._sweep_stuck_items", lambda bridge, task_mgr: None)
    monkeypatch.setattr("talk.CONTROL_RUNTIME_DB_ENABLED", False)
    monkeypatch.setattr("core._is_meta_command", lambda content: False)
    monkeypatch.setattr("core._talk_slug", lambda content, task_id: task_id)
    external_inputs = []
    monkeypatch.setattr("evaluation.emptiness.on_external_input", lambda user_id: external_inputs.append(user_id))

    core.do_talk()

    assert ang.processed == []
    assert liquan.processed == []
    assert ang.poll_calls == 1
    assert liquan.poll_calls == 0
    assert external_inputs == ["default"]


def test_do_talk_stops_retry_when_retry_ceiling_reached(monkeypatch):
    import core
    import evaluation.emptiness as emptiness

    if core.Mira is None:
        pytest.skip("mira_bridge not available (CI)")

    class FakeMessage:
        def __init__(self):
            self.id = "followup_1"
            self.thread_id = "req_old"
            self.sender = "user"
            self.content = "retry please"
            self.user_id = "default"

        def to_dict(self):
            return {
                "id": self.id,
                "thread_id": self.thread_id,
                "sender": self.sender,
                "content": self.content,
                "user_id": self.user_id,
            }

    class FakeBridge:
        def __init__(self):
            self.user_id = "default"
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
    monkeypatch.setattr("talk.TaskManager", FakeTaskManager)
    monkeypatch.setattr("talk._sweep_stuck_items", lambda bridge, task_mgr: None)
    monkeypatch.setattr("talk.CONTROL_RUNTIME_DB_ENABLED", False)
    monkeypatch.setattr("core._is_meta_command", lambda content: False)
    monkeypatch.setattr("core._talk_slug", lambda content, task_id: task_id)
    monkeypatch.setattr("evaluation.emptiness.on_external_input", lambda user_id: None)

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

    monkeypatch.setattr(
        "writing.check_writing_responses",
        lambda: [
            {"workspace": workspace_a, "project": {"title": "Plan", "phase": "plan_ready"}},
            {"workspace": workspace_b, "project": {"title": "Draft", "phase": "draft_ready"}},
        ],
    )
    monkeypatch.setattr("writing.advance_project", lambda workspace: advanced.append(workspace))
    monkeypatch.setattr("writing._maybe_promote_daily_collab_seed_to_draft", lambda: None)
    monkeypatch.setattr("writing.WRITING_PIPELINE_STATUS_FILE", tmp_path / "writing_pipeline_status.json")

    count = core._run_canonical_writing_pipeline()

    assert count == 1
    assert advanced == [workspace_a]


def test_canonical_writing_pipeline_promotes_daily_collab_seed_to_review_draft(monkeypatch, tmp_path):
    import core
    import writing

    manifest = {"articles": {}}
    runner_calls = []
    seed = {
        "id": "receipts-trust",
        "title": "I Had Receipts, But Not Trust",
        "human_signal": "The manifest has approval_required=0 and the pipeline keeps advancing 0 project(s).",
        "mira_signal": "The V5 discussion seed was never promoted into a draft.",
        "why_now": "The public writing lane is empty.",
    }

    def fake_runner(task_id, title, writing_type, idea):
        runner_calls.append(
            {
                "task_id": task_id,
                "title": title,
                "writing_type": writing_type,
                "idea": idea,
            }
        )
        manifest["articles"]["my-receipts-did-not-become-trust"] = {
            "item_id": task_id,
            "title": title,
            "status": "approval_required",
        }

    monkeypatch.setattr("writing.check_writing_responses", lambda: [])
    monkeypatch.setattr(writing, "WRITING_PIPELINE_STATUS_FILE", tmp_path / "writing_pipeline_status.json")
    monkeypatch.setattr(writing, "DAILY_COLLAB_AUTOWRITE_PROMOTIONS_FILE", tmp_path / "promotions.jsonl")
    monkeypatch.setattr(writing, "DAILY_COLLAB_AUTOWRITE_LOCK_FILE", tmp_path / "promotion.lock")
    monkeypatch.setattr(writing, "_load_publish_manifest_for_writing", lambda: manifest)
    monkeypatch.setattr(writing, "_select_daily_collab_seed_for_autowrite", lambda: seed)
    monkeypatch.setattr(writing, "_get_daily_collab_autowrite_runner", lambda: fake_runner)

    count = core._run_canonical_writing_pipeline()

    assert count == 1
    assert runner_calls
    call = runner_calls[0]
    assert call["task_id"] == "autowrite_v5_receipts-trust"
    assert call["title"] == "My Receipts Did Not Become Trust"
    assert call["writing_type"] == "essay"
    assert "- **platform**: Substack" in call["idea"]
    assert "- **language**: en" in call["idea"]
    assert 'Refer to the user only as "my human"' in call["idea"]
    status = json.loads((tmp_path / "writing_pipeline_status.json").read_text(encoding="utf-8"))
    assert status["advanced"] == 1
    assert status["selected"] == ["My Receipts Did Not Become Trust"]


def test_canonical_writing_pipeline_skips_daily_collab_when_review_draft_waits(monkeypatch, tmp_path):
    import core
    import writing

    manifest = {
        "articles": {
            "waiting": {
                "item_id": "autowrite_v5_existing",
                "title": "Waiting Draft",
                "status": "approval_required",
            }
        }
    }

    monkeypatch.setattr("writing.check_writing_responses", lambda: [])
    monkeypatch.setattr(writing, "WRITING_PIPELINE_STATUS_FILE", tmp_path / "writing_pipeline_status.json")
    monkeypatch.setattr(writing, "_load_publish_manifest_for_writing", lambda: manifest)
    monkeypatch.setattr(
        writing,
        "_select_daily_collab_seed_for_autowrite",
        lambda: pytest.fail("seed selection should be skipped while review draft waits"),
    )

    count = core._run_canonical_writing_pipeline()

    assert count == 0
    status = json.loads((tmp_path / "writing_pipeline_status.json").read_text(encoding="utf-8"))
    assert status["advanced"] == 0


def test_daily_collab_promotion_does_not_duplicate_manifest_task(monkeypatch, tmp_path):
    import writing

    seed = {"id": "receipts-trust", "title": "I Had Receipts, But Not Trust"}
    manifest = {
        "articles": {
            "existing": {
                "item_id": "autowrite_v5_receipts-trust",
                "title": "My Receipts Did Not Become Trust",
                "status": "blocked_writer_gate",
            }
        }
    }

    monkeypatch.setattr(writing, "DAILY_COLLAB_AUTOWRITE_PROMOTIONS_FILE", tmp_path / "promotions.jsonl")
    monkeypatch.setattr(writing, "_load_publish_manifest_for_writing", lambda: manifest)
    monkeypatch.setattr(writing, "_select_daily_collab_seed_for_autowrite", lambda: seed)
    monkeypatch.setattr(
        writing,
        "_get_daily_collab_autowrite_runner",
        lambda: pytest.fail("duplicate manifest task should not run autowrite"),
    )

    assert writing._maybe_promote_daily_collab_seed_to_draft() is None


def test_writing_triage_parks_stale_interrupted_projects(monkeypatch, tmp_path):
    import writing

    workspace = tmp_path / "old-project"
    drafts = workspace / "versions" / "v1" / "drafts"
    drafts.mkdir(parents=True)
    (drafts / "draft_claude.md").write_text("draft", encoding="utf-8")
    project = {
        "title": "Old Project",
        "phase": "reviewing",
        "version": 1,
        "updated": "2026-04-01T00:00:00",
        "last_advanced_at": "2026-04-01T00:00:00",
    }
    (workspace / "project.json").write_text(json.dumps(project), encoding="utf-8")

    recent = tmp_path / "recent-project"
    recent.mkdir()
    recent_project = {
        "title": "Recent Project",
        "phase": "reviewing",
        "updated": datetime.now().isoformat(),
    }
    (recent / "project.json").write_text(json.dumps(recent_project), encoding="utf-8")

    monkeypatch.setattr(
        writing,
        "check_writing_responses",
        lambda: [
            {"workspace": workspace, "project": project},
            {"workspace": recent, "project": recent_project},
        ],
    )
    monkeypatch.setattr(writing, "WRITING_TRIAGE_STATUS_FILE", tmp_path / "writing_triage_status.json")

    status = writing.triage_stalled_writing_projects(min_age_days=7)

    assert status["parked_count"] == 1
    assert status["kept_count"] == 1
    parked = json.loads((workspace / "project.json").read_text(encoding="utf-8"))
    assert parked["phase"] == "stale_triage"
    assert parked["stale_triage"]["previous_phase"] == "reviewing"
    assert parked["stale_triage"]["artifacts"]["draft_count"] == 1
    assert parked["last_advanced_at"] == "2026-04-01T00:00:00"
    kept = json.loads((recent / "project.json").read_text(encoding="utf-8"))
    assert kept["phase"] == "reviewing"


def test_run_autowrite_pipeline_queues_for_publication_approval(monkeypatch, tmp_path):
    """V5: autowrite queues strong drafts for human publication approval."""
    from workflows import writing
    import publish.manifest as publish_manifest

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
    manifest_updates = []

    def fake_update_manifest(slug, **fields):
        manifest_updates.append((slug, fields))
        return {"slug": slug, **fields}

    monkeypatch.setattr("workflows.writing._TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr("workflows.writing.Mira", lambda: bridge)
    monkeypatch.setattr(
        "workflows.writing.run_full_pipeline",
        lambda title, body: (project_dir, final_file.read_text(encoding="utf-8")),
    )
    monkeypatch.setattr(publish_manifest, "update_manifest", fake_update_manifest)
    # Belt-and-suspenders: redirect any unmocked manifest writes to a tmp file
    # so they can never leak into the real iCloud manifest.
    monkeypatch.setenv("MIRA_PUBLISH_MANIFEST_PATH", str(tmp_path / "publish_manifest.json"))

    writing.run_autowrite_pipeline("autowrite_2026-04-05", "Test Essay", "essay", "idea body")

    task_ws = tmp_path / "tasks" / "autowrite_2026-04-05"
    meta = json.loads((task_ws / "autowrite_meta.json").read_text(encoding="utf-8"))
    assert meta["slug"] == project_dir.name
    assert meta["final_md"] == str(final_file)
    # Bridge task waits for publication review instead of pretending publish is queued.
    assert bridge.calls
    assert bridge.calls[-1][1] == "needs-input"
    # Manifest is not approved until human publication approval is recorded.
    assert manifest_updates, "expected update_manifest to be called"
    assert manifest_updates[-1][1].get("status") == "approval_required"
    assert manifest_updates[-1][1].get("publication_gate") == "human_review_required"


def test_run_autowrite_pipeline_blocks_weak_substack_draft(monkeypatch, tmp_path):
    """Substack autowrite should fail quality gate before manifest approval."""
    from workflows import writing
    import publish.manifest as publish_manifest

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    final_file = project_dir / "final.md"
    final_file.write_text("# Thoughts\n\nIn this essay, I explore trust in AI.\n", encoding="utf-8")

    class FakeBridge:
        def __init__(self):
            self.calls = []

        def update_task_status(self, task_id, status, agent_message=""):
            self.calls.append((task_id, status, agent_message))

    bridge = FakeBridge()
    manifest_updates = []

    monkeypatch.setattr("workflows.writing._TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr("workflows.writing.Mira", lambda: bridge)
    monkeypatch.setattr(
        "workflows.writing.run_full_pipeline",
        lambda title, body: (project_dir, final_file.read_text(encoding="utf-8")),
    )
    monkeypatch.setattr(
        publish_manifest,
        "update_manifest",
        lambda slug, **fields: manifest_updates.append((slug, fields)) or {"slug": slug, **fields},
    )
    monkeypatch.setenv("MIRA_PUBLISH_MANIFEST_PATH", str(tmp_path / "publish_manifest.json"))

    idea = "# Weak\n\n- **platform**: Substack\n\n## Thesis\n\nGeneric AI trust article."
    writing.run_autowrite_pipeline("autowrite_2026-06-03", "Thoughts", "essay", idea)

    assert (project_dir / "substack_quality_report.json").exists()
    assert manifest_updates
    assert manifest_updates[-1][1]["status"] == "blocked_writer_gate"
    assert not any(update[1].get("status") == "approved" for update in manifest_updates)
    assert bridge.calls[-1][1] == "error"


def test_run_autowrite_pipeline_queues_strong_substack_draft_with_subtitle(monkeypatch, tmp_path):
    """Strong Substack drafts carry the quality-checked subtitle into approval-required manifest rows."""
    from workflows import writing
    import publish.manifest as publish_manifest

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    final_file = project_dir / "final.md"
    final_file.write_text(
        """# My Agent Said Done Before It Had Proof

*The app looked settled because activity was mistaken for proof.*

Last week I traced a Mira task through the app, the thread, and the task record after the worker had failed. I only caught the failure because the app stayed cheerful while the logs were dead.

The useful lesson was not that agents fail. Everyone knows that. The useful lesson was that the interface can become the lie if the status model treats activity as evidence.

## What The Status Hid

I traced the thread through the task record, the bridge item, and the app reply path. The failure was not a missing model call. It was a state transition that made a human-visible promise before verification existed.

## A Better Rule

A reliable agent needs a standard: done means the observable user outcome exists. If the outcome is not checked, the honest state is still running or unverified.
""",
        encoding="utf-8",
    )

    class FakeBridge:
        def __init__(self):
            self.calls = []

        def update_task_status(self, task_id, status, agent_message=""):
            self.calls.append((task_id, status, agent_message))

    bridge = FakeBridge()
    manifest_updates = []

    monkeypatch.setattr("workflows.writing._TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr("workflows.writing.Mira", lambda: bridge)
    monkeypatch.setattr(
        "workflows.writing.run_full_pipeline",
        lambda title, body: (project_dir, final_file.read_text(encoding="utf-8")),
    )
    monkeypatch.setattr(
        publish_manifest,
        "update_manifest",
        lambda slug, **fields: manifest_updates.append((slug, fields)) or {"slug": slug, **fields},
    )
    monkeypatch.setenv("MIRA_PUBLISH_MANIFEST_PATH", str(tmp_path / "publish_manifest.json"))

    idea = "# Strong\n\n- **platform**: Substack\n\n## Thesis\n\nDone means the user-visible outcome exists."
    writing.run_autowrite_pipeline("autowrite_2026-06-03", "Strong", "essay", idea)

    assert (project_dir / ".substack_article_packet.json").exists()
    assert (project_dir / "substack_quality_report.json").exists()
    assert manifest_updates
    queued = manifest_updates[-1][1]
    assert queued["status"] == "approval_required"
    assert queued["subtitle"] == "The app looked settled because activity was mistaken for proof."
    assert queued["publication_gate"] == "human_review_required"
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
        lambda: lambda task_id, title, writing_type, idea: captured.update(
            {
                "task_id": task_id,
                "title": title,
                "writing_type": writing_type,
                "idea": idea,
            }
        ),
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
        lambda title, writing_type, idea_content: captured.update(
            {
                "title": title,
                "writing_type": writing_type,
                "idea_content": idea_content,
            }
        ),
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
