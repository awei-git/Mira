from __future__ import annotations

import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

SERVER_PATH = Path(__file__).resolve().parent.parent.parent / "web" / "server.py"
SPEC = importlib.util.spec_from_file_location("mira_web_server_for_tests", SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(server)


def _make_client(
    monkeypatch, tmp_path: Path, *, token: str = "", allow_loopback: bool = True, profiles: dict | None = None
) -> TestClient:
    bridge = tmp_path / "bridge"
    users_dir = bridge / "users"
    (users_dir / "ang" / "items").mkdir(parents=True)
    (users_dir / "ang" / "commands").mkdir(parents=True)
    (users_dir / "liquan" / "items").mkdir(parents=True)
    icloud = tmp_path / "icloud"
    bridge.mkdir(exist_ok=True)
    icloud.mkdir(exist_ok=True)
    if profiles is not None:
        (bridge / "profiles.json").write_text(json.dumps(profiles), encoding="utf-8")

    monkeypatch.setattr(server, "BRIDGE", bridge)
    monkeypatch.setattr(server, "USERS_DIR", users_dir)
    monkeypatch.setattr(server, "_ICLOUD_ARTIFACTS", icloud)
    monkeypatch.setattr(server, "WEBGUI_TOKEN", token)
    monkeypatch.setattr(server, "WEBGUI_ALLOW_LOOPBACK_WITHOUT_TOKEN", allow_loopback)
    monkeypatch.setattr(server, "get_known_user_ids", lambda: ["ang", "liquan"])
    monkeypatch.setattr(server, "is_known_user", lambda user_id: user_id in {"ang", "liquan"})
    monkeypatch.setattr(server, "get_user_config", lambda user_id: {"display_name": user_id.title()})
    server._rate_buckets.clear()
    return TestClient(server.app)


def _command_files(tmp_path: Path, user_id: str = "ang") -> list[Path]:
    return sorted((tmp_path / "bridge" / "users" / user_id / "commands").glob("*.json"))


def test_web_api_rejects_unknown_user(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path)
    resp = client.get("/api/ghost/items")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Unknown user"


def test_web_api_allows_loopback_without_token(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path, token="", allow_loopback=True)
    resp = client.get("/api/ang/items")
    assert resp.status_code == 200
    assert resp.json() == []


def test_concurrent_todo_deletes_keep_json_valid(monkeypatch, tmp_path: Path):
    _make_client(monkeypatch, tmp_path)
    path = tmp_path / "bridge" / "users" / "ang" / "todos.json"
    path.write_text(
        json.dumps(
            [
                {"id": "todo_a", "title": "A", "status": "working"},
                {"id": "todo_b", "title": "B", "status": "working"},
                {"id": "todo_c", "title": "C", "status": "done"},
            ]
        ),
        encoding="utf-8",
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda todo_id: server.delete_todo("ang", todo_id), ["todo_a", "todo_b"]))

    todos = json.loads(path.read_text(encoding="utf-8"))
    assert [t["id"] for t in todos] == ["todo_c"]


def test_web_api_requires_token_when_configured(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path, token="secret-token", allow_loopback=True)
    blocked = client.get("/api/ang/items")
    allowed = client.get("/api/ang/items", headers={"X-Mira-Token": "secret-token"})
    assert blocked.status_code == 401
    assert allowed.status_code == 200


def test_read_rate_limit_does_not_block_user_reply(monkeypatch, tmp_path: Path):
    import control.db as control_db
    import control.repository as control_repository

    client = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "_READ_RATE_LIMIT", 1)
    monkeypatch.setattr(server, "_WRITE_RATE_LIMIT", 10)
    monkeypatch.setattr(server, "CONTROL_API_WRITES_ENABLED", True)
    monkeypatch.setattr(server, "ICLOUD_COMMAND_FALLBACK_ENABLED", False)
    calls = {}

    class FakeTx:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeRepo:
        def __init__(self, conn):
            self.conn = conn

        def append_user_reply(self, **kwargs):
            calls["append_user_reply"] = kwargs
            return {
                "id": kwargs["task_id"],
                "type": "discussion",
                "title": "Thread",
                "status": "queued",
                "tags": [],
                "origin": "user",
                "pinned": False,
                "quick": False,
                "parent_id": None,
                "created_at": kwargs["created_at"],
                "updated_at": kwargs["created_at"],
                "messages": [{"id": kwargs["message_id"], "sender": kwargs["sender"], "content": kwargs["content"]}],
                "error": None,
                "result_path": None,
            }

    monkeypatch.setattr(control_repository, "ControlRepository", FakeRepo)
    monkeypatch.setattr(control_db, "transaction", lambda: FakeTx())

    assert client.get("/api/ang/items").status_code == 200
    assert client.get("/api/ang/items").status_code == 429

    resp = client.post("/api/ang/tasks/feed_chat_20260504/reply", json={"content": "还在吗"})

    assert resp.status_code == 200
    assert calls["append_user_reply"]["task_id"] == "feed_chat_20260504"
    assert calls["append_user_reply"]["content"] == "还在吗"


def test_liveness_endpoints_bypass_read_rate_limit(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "_READ_RATE_LIMIT", 1)

    assert client.get("/api/ang/items").status_code == 200
    assert client.get("/api/ang/items").status_code == 429

    assert client.get("/api/heartbeat").status_code == 200
    assert client.get("/api/ang/manifest").status_code == 200


def test_profiles_are_filtered_to_known_users(monkeypatch, tmp_path: Path):
    client = _make_client(
        monkeypatch,
        tmp_path,
        profiles={
            "profiles": [
                {"id": "ang", "display_name": "Ang", "agent_name": "Mira"},
                {"id": "intruder", "display_name": "Intruder", "agent_name": "Mallory"},
            ]
        },
    )
    resp = client.get("/api/profiles")
    assert resp.status_code == 200
    assert resp.json() == {"profiles": [{"id": "ang", "display_name": "Ang", "agent_name": "Mira"}]}


def test_heartbeat_top_level_status_uses_task_manager(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path)
    bridge = tmp_path / "bridge"
    (bridge / "heartbeat.json").write_text(
        json.dumps(
            {
                "timestamp": "2026-05-01T14:16:25Z",
                "status": "online",
                "busy": False,
                "active_count": 0,
                "agent_status": {"busy": False, "active_count": 0, "active_tasks": []},
            }
        ),
        encoding="utf-8",
    )

    class FakeTaskManager:
        def get_status_summary(self):
            return {
                "busy": True,
                "active_count": 1,
                "active_tasks": [{"task_id": "req_todo_1cacf0e3"}],
                "last_completed": "2026-04-30T13:34:34Z",
            }

    monkeypatch.setattr(server, "_task_manager", lambda: FakeTaskManager())

    resp = client.get("/api/heartbeat")

    assert resp.status_code == 200
    data = resp.json()
    assert data["busy"] is True
    assert data["active_count"] == 1
    assert data["active_tasks"] == [{"task_id": "req_todo_1cacf0e3"}]
    assert data["agent_status"]["busy"] is True


def test_v3_dashboard_endpoint_returns_config(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path)

    resp = client.get("/api/ang/v3")

    assert resp.status_code == 200
    body = resp.json()
    assert body["dashboard"]["hard_policy_count"] == 43
    assert body["dashboard"]["soft_policy_count"] == 9
    assert len(body["dashboard"]["active_pipelines"]) == 21
    assert body["config"]["policy_parameters"]["max_concurrent_pipelines"] == 5


def test_backend_dashboard_endpoint_returns_technical_snapshot(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path)

    resp = client.get("/api/ang/backend-dashboard")

    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"]["id"] == "ang"
    assert body["service"]["web"]["port"] == server.WEBGUI_PORT
    assert body["policies"]["hard"] == 43
    assert body["policies"]["soft"] == 9
    assert len(body["pipelines"]) == 21
    allowed_statuses = {"green", "red", "yellow", "blue", "gray"}
    for pipeline in body["pipelines"]:
        for step in pipeline["steps"]:
            assert step["status"] in allowed_statuses
            assert step["model"] or step["model_source"] == "no LLM"
    assert set(body["memory"]) == {"status", "kernel", "ledger", "commits", "effects", "queues"}
    assert {"artifacts", "recent_items", "jobs"} <= set(body["outputs"])
    assert "security" in body
    assert "agent_stats" in body["outputs"]["jobs"]
    assert {"kernel", "ledger", "commits", "effect_log", "eval_history", "snapshots", "artifacts"} <= set(body["paths"])


def test_backend_dashboard_preserves_string_failure_message():
    from mira.kernel.delta import MemoryDeltaProposal
    from mira.kernel.ledger import ExperienceRecord
    from mira.pipelines import PIPELINE_CATALOG

    failure = "PREFLIGHT BLOCKED [secret]: missing file"
    record = ExperienceRecord(
        id="communication_test_failure",
        pipeline="communication",
        trigger="event",
        intent="test communication failure",
        outcome="failed",
        delta=MemoryDeltaProposal(
            pipeline="communication",
            run_id="communication_test_failure",
            memory_class="operational",
            what_happened="Task task142 finished with status failed",
            what_mattered=failure,
            what_changed="Future communication snapshots include task outcome task142",
            what_failed=failure,
            actions=[],
        ),
        causal_links=[],
        confidence=1.0,
        memory_class="operational",
    )

    rows = server._pipeline_status_rows(
        "ang",
        {"communication": PIPELINE_CATALOG["communication"]},
        [record],
        [],
        [],
        {"jobs": []},
        {"models": []},
    )

    assert rows[0]["status_text"] == failure
    assert rows[0]["status_detail"] == failure
    assert rows[0]["error"] == failure
    assert rows[0]["steps"][-1]["error"] == failure


def test_backend_dashboard_uses_podcast_artifacts_as_pipeline_evidence(monkeypatch, tmp_path: Path):
    from mira.pipelines import PIPELINE_CATALOG

    monkeypatch.setattr(server, "_ICLOUD_ARTIFACTS", tmp_path)
    user_root = tmp_path / "ang"
    writings = user_root / "writings"
    audio = user_root / "audio" / "podcast"
    (audio / "en" / "episode-slug").mkdir(parents=True)
    (audio / "zh" / "episode-slug").mkdir(parents=True)
    writings.mkdir(parents=True)
    (audio / "en" / "episode-slug" / "episode.mp3").write_bytes(b"mp3")
    (audio / "zh" / "episode-slug" / "episode.mp3").write_bytes(b"mp3")
    (writings / "publish_manifest.json").write_text(
        json.dumps(
            {
                "articles": {
                    "essay": {
                        "title": "Essay",
                        "status": "complete",
                        "podcast_slug": "episode-slug",
                        "timestamps": {
                            "podcast_en": "2026-05-15T16:36:46Z",
                            "podcast_zh": "2026-05-15T17:06:13Z",
                            "complete": "2026-05-15T17:06:59Z",
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    rows = server._pipeline_status_rows(
        "ang",
        {"podcast_production": PIPELINE_CATALOG["podcast_production"]},
        [],
        [],
        [],
        {"jobs": []},
        {"models": []},
    )

    assert rows[0]["status"] == "green"
    assert rows[0]["last_success_at"] == "2026-05-15T17:06:59Z"
    assert rows[0]["outputs"][0]["status"] == "done"
    assert rows[0]["outputs"][0]["title"] == "Essay (EN+ZH)"
    tts_step = next(
        step for step in rows[0]["steps"] if step["name"] == "language_detect_tts_route_synthesis_postprocess"
    )
    assert tts_step["model"] == "EN: Gemini 3.1 Flash TTS Preview / ZH: MiniMax Speech 2.8 HD"
    assert tts_step["model_source"] == "step policy"
    assert tts_step["model_recorded"] is False


def test_backend_dashboard_does_not_invent_step_usage_from_pipeline_aggregate():
    from mira.pipelines import PIPELINE_CATALOG

    rows = server._pipeline_status_rows(
        "ang",
        {"article_creation": PIPELINE_CATALOG["article_creation"]},
        [],
        [],
        [],
        {
            "jobs": [
                {
                    "name": "writing-pipeline",
                    "enabled": True,
                    "status": "done",
                    "usage": {
                        "calls": 2,
                        "tokens": 12000,
                        "cost_usd": 0.42,
                        "models": {"claude-sonnet-4-6": {"calls": 2, "tokens": 12000, "cost_usd": 0.42}},
                    },
                }
            ]
        },
        {"models": [{"agent": "writer", "model": "claude-sonnet-4-6"}]},
    )

    assert rows[0]["usage"]["tokens"] == 12000
    draft_step = next(step for step in rows[0]["steps"] if step["name"] == "draft")
    assert draft_step["tokens"] == 0
    assert draft_step["cost_usd"] == 0
    assert draft_step["usage_recorded"] is False
    assert draft_step["usage_scope"] == "pipeline aggregate only; exact per-step usage is not instrumented"
    assert draft_step["model"] == "claude-sonnet-4-6"
    assert draft_step["model_recorded"] is False
    assert draft_step["model_source"] == "agent policy"


def test_book_reading_pipeline_labels_match_actual_refinement_flow():
    from mira.pipelines import PIPELINE_CATALOG

    step_names = [step.name for step in PIPELINE_CATALOG["book_reading_notes"].steps]

    assert "compile_notes_de_ai" not in step_names
    assert "voice_refinement_pass" in step_names
    assert "epub_language_cleanup" in step_names

    rows = server._pipeline_status_rows(
        "ang",
        {"book_reading_notes": PIPELINE_CATALOG["book_reading_notes"]},
        [],
        [],
        [],
        {"jobs": []},
        {"models": [{"agent": "reader", "model": "claude-sonnet-4-6"}]},
    )
    draft_step = next(step for step in rows[0]["steps"] if step["name"] == "draft_reading_report")
    refine_step = next(step for step in rows[0]["steps"] if step["name"] == "voice_refinement_pass")
    cleanup_step = next(step for step in rows[0]["steps"] if step["name"] == "epub_language_cleanup")
    assert draft_step["model"] == "gpt5 / claude heavy fallback"
    assert refine_step["model"] == "claude heavy tier"
    assert cleanup_step["model"] == "deepseek cleanup when translation is needed"


def test_codex_cli_observations_are_reported_from_runtime_logs(tmp_path: Path):
    from datetime import date

    log_path = tmp_path / "bg-daily-research.log"
    today = date.today().isoformat()
    log_path.write_text(
        f"{today} 14:35:21,575 [INFO] Codex CLI call: gpt-5.5 -> 546 chars\n"
        f"{today} 14:36:07,093 [INFO] Codex CLI call: gpt-5.5 -> 551 chars\n"
        "2020-01-01 00:00:00,000 [INFO] Codex CLI call: old-model -> 100 chars\n",
        encoding="utf-8",
    )

    rows = server._codex_cli_observations(tmp_path, days=2)

    assert rows[today]["calls"] == 2
    assert rows[today]["output_chars"] == 1097
    assert rows[today]["models"]["gpt-5.5"] == {"calls": 2, "output_chars": 1097}


def test_codex_cli_provider_writes_usage_record(monkeypatch, tmp_path: Path):
    import llm
    import llm_providers.codex as codex

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        out_path = Path(cmd[cmd.index("--output-last-message") + 1])
        out_path.write_text("codex answer", encoding="utf-8")
        return Result()

    monkeypatch.setattr(codex.subprocess, "run", fake_run)
    monkeypatch.setattr(llm, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(llm, "TOKEN_USAGE_LOG_PATH", tmp_path / "token_usage.jsonl")
    llm.set_usage_agent("codex-test")

    result = codex.codex_think("hello world", model_id="gpt-5.5", timeout=5)

    usage_files = list(tmp_path.glob("usage_*.jsonl"))
    assert result == "codex answer"
    assert len(usage_files) == 1
    record = json.loads(usage_files[0].read_text(encoding="utf-8").splitlines()[0])
    assert record["agent"] == "codex-test"
    assert record["provider"] == "codex_cli"
    assert record["model"] == "gpt-5.5"
    assert record["estimated"] is True
    assert record["total_tokens"] > 0


def test_security_alert_summary_includes_concrete_skill_audit_action():
    item = {
        "id": "skill_audit_blocked_test",
        "type": "alert",
        "title": "Skill audit blocked: adversarial-market-flywheel",
        "status": "failed",
        "tags": ["security", "skill_audit", "error"],
        "updated_at": "2026-05-15T23:05:23.686Z",
        "messages": [
            {
                "content": json.dumps(
                    {
                        "event": "skill_audit_blocked",
                        "skill_name": "adversarial-market-flywheel",
                        "failed_check": "missing_epistemic_audit_metadata",
                        "failed_checks": ["missing_epistemic_audit_metadata"],
                    }
                )
            }
        ],
    }

    summary = server._dashboard_item_summary("ang", item)

    assert "keep 'adversarial-market-flywheel' blocked" in summary["action"]
    assert "provenance" in summary["action"]
    assert "re-run the skill audit" in summary["action"]


def test_backend_dashboard_shell_and_static_assets_are_served(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path)

    shell = client.get("/backend/pipelines")
    asset = client.get("/backend-assets/app.js")

    assert shell.status_code == 200
    assert '<script type="module" src="/backend-assets/app.js"></script>' in shell.text
    assert asset.status_code == 200
    assert "loadDashboard" in asset.text


def test_backend_dashboard_model_update_persists_override(monkeypatch, tmp_path: Path):
    import mira.runtime as runtime

    client = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "CONTROL_API_WRITES_ENABLED", True)
    config_root = tmp_path / "v3-config"
    monkeypatch.setattr(runtime, "default_v3_paths", lambda: SimpleNamespace(root=config_root))

    resp = client.post(
        "/api/ang/backend-dashboard/models/writer",
        json={"model": "claude-sonnet-4-6", "token_budget": 128000},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["assignment"]["model"] == "claude-sonnet-4-6"
    overrides = json.loads((config_root / "model_assignments.json").read_text(encoding="utf-8"))
    assert overrides["writer"]["token_budget"] == 128000
    assert overrides["writer"]["updated_by"] == "ang"


def test_backend_dashboard_model_update_rejects_unknown_model(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "CONTROL_API_WRITES_ENABLED", True)

    resp = client.post(
        "/api/ang/backend-dashboard/models/writer",
        json={"model": "../../not-a-model", "token_budget": 128000},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unknown model option"


def test_safe_join_rejects_parent_traversal(tmp_path: Path):
    base = tmp_path / "artifacts"
    base.mkdir()
    with pytest.raises(HTTPException):
        server._safe_join(base, "..")


def test_artifact_routes_reject_unknown_top_level_sections(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path)
    secret_dir = tmp_path / "icloud" / "ang" / "secrets"
    secret_dir.mkdir(parents=True)
    (secret_dir / "notes.txt").write_text("classified", encoding="utf-8")

    resp = client.get("/api/ang/artifacts/secrets")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Artifact section not found"


def test_artifact_routes_allow_listed_shared_sections_only(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path)
    shared_briefing = tmp_path / "icloud" / "shared" / "briefings"
    shared_briefing.mkdir(parents=True)
    (shared_briefing / "daily.md").write_text("shared briefing", encoding="utf-8")
    shared_secret = tmp_path / "icloud" / "shared" / "internal"
    shared_secret.mkdir(parents=True)
    (shared_secret / "ops.md").write_text("do not expose", encoding="utf-8")

    shared_root = client.get("/api/ang/artifacts/shared")
    allowed = client.get("/api/ang/artifacts/shared/briefings")
    blocked = client.get("/api/ang/artifacts/shared/internal")
    file_read = client.get("/api/ang/artifacts/shared/briefings/daily.md")

    assert shared_root.status_code == 200
    shared_entries = shared_root.json()
    assert len(shared_entries) == 1
    assert shared_entries[0]["name"] == "briefings/"
    assert allowed.status_code == 200
    data = allowed.json()
    assert len(data) == 1
    assert data[0]["name"] == "daily.md"
    assert data[0]["size"] == 15
    assert isinstance(data[0]["modified"], str)
    assert blocked.status_code == 404
    assert blocked.json()["detail"] == "Artifact section not found"
    assert file_read.status_code == 200
    assert file_read.text == "shared briefing"


def test_reply_requires_existing_item_and_does_not_enqueue_command(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "ICLOUD_COMMAND_FALLBACK_ENABLED", True)

    resp = client.post("/api/ang/items/missing/reply", json={"content": "hello"})

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Item not found"


def test_tasks_endpoint_uses_control_projection_without_mutating_bridge(monkeypatch, tmp_path: Path):
    import control.db as control_db
    import control.repository as control_repository

    client = _make_client(monkeypatch, tmp_path)
    item_path = tmp_path / "bridge" / "users" / "ang" / "items" / "req_123.json"
    item_path.write_text(
        json.dumps(
            {
                "id": "req_123",
                "type": "request",
                "title": "Existing task",
                "status": "working",
                "origin": "user",
                "tags": [],
                "quick": False,
                "pinned": False,
                "created_at": "2026-04-30T00:00:00Z",
                "updated_at": "2026-04-30T00:00:00Z",
                "messages": [],
            }
        ),
        encoding="utf-8",
    )
    before = item_path.read_text(encoding="utf-8")
    calls = {}

    class FakeTx:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeRepo:
        def __init__(self, conn):
            self.conn = conn

        def list_items(self, user_id, *, include_archived=False, limit=200, messages_per_item=None):
            calls["list"] = (user_id, include_archived, limit, messages_per_item)
            return [{"id": "req_123", "type": "request", "title": "Existing task", "status": "working"}]

        def last_event_id(self, user_id):
            return 42

    def fake_sync(user_id, *, user_dir, task_status_file):
        calls["sync"] = (user_id, user_dir, task_status_file)

    monkeypatch.setattr(control_repository, "sync_user_from_legacy", fake_sync)
    monkeypatch.setattr(control_repository, "ControlRepository", FakeRepo)
    monkeypatch.setattr(control_db, "transaction", lambda: FakeTx())

    resp = client.get("/api/ang/tasks")

    assert resp.status_code == 200
    assert resp.json()["items"] == [{"id": "req_123", "type": "request", "title": "Existing task", "status": "working"}]
    assert resp.json()["last_event_id"] == 42
    assert calls["sync"][0] == "ang"
    assert calls["list"] == ("ang", False, 200, 20)
    assert item_path.read_text(encoding="utf-8") == before
    assert _command_files(tmp_path) == []


def test_tasks_endpoint_hides_internal_liveness_items(monkeypatch, tmp_path: Path):
    import control.db as control_db
    import control.repository as control_repository

    client = _make_client(monkeypatch, tmp_path)

    class FakeTx:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeRepo:
        def __init__(self, conn):
            self.conn = conn

        def list_items(self, user_id, *, include_archived=False, limit=200, messages_per_item=None):
            return [
                {
                    "id": "mira_liveness_task_dispatch",
                    "type": "discussion",
                    "title": "Output Liveness: task_dispatch stale",
                    "status": "done",
                    "tags": ["system", "liveness"],
                },
                {"id": "req_123", "type": "request", "title": "Existing task", "status": "working"},
            ]

        def last_event_id(self, user_id):
            return 42

    monkeypatch.setattr(control_repository, "sync_user_from_legacy", lambda *args, **kwargs: None)
    monkeypatch.setattr(control_repository, "ControlRepository", FakeRepo)
    monkeypatch.setattr(control_db, "transaction", lambda: FakeTx())

    resp = client.get("/api/ang/tasks")

    assert resp.status_code == 200
    assert resp.json()["items"] == [{"id": "req_123", "type": "request", "title": "Existing task", "status": "working"}]


def test_task_detail_endpoint_uses_control_projection(monkeypatch, tmp_path: Path):
    import control.db as control_db
    import control.repository as control_repository

    client = _make_client(monkeypatch, tmp_path)
    calls = {}

    class FakeTx:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeRepo:
        def __init__(self, conn):
            self.conn = conn

        def get_item(self, user_id, task_id, messages_per_item=None):
            calls["get"] = (user_id, task_id, messages_per_item)
            return {"id": task_id, "type": "request", "title": "Existing task", "status": "working"}

    def fake_sync(user_id, *, user_dir, task_status_file):
        calls["sync"] = (user_id, user_dir, task_status_file)

    monkeypatch.setattr(control_repository, "sync_user_from_legacy", fake_sync)
    monkeypatch.setattr(control_repository, "ControlRepository", FakeRepo)
    monkeypatch.setattr(control_db, "transaction", lambda: FakeTx())

    resp = client.get("/api/ang/tasks/req_123?messages_per_item=12")

    assert resp.status_code == 200
    assert resp.json()["item"]["id"] == "req_123"
    assert calls["sync"][0] == "ang"
    assert calls["get"] == ("ang", "req_123", 12)
    assert _command_files(tmp_path) == []


def test_threads_endpoint_uses_control_projection(monkeypatch, tmp_path: Path):
    import control.db as control_db
    import control.repository as control_repository

    client = _make_client(monkeypatch, tmp_path)
    calls = {}

    class FakeTx:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeRepo:
        def __init__(self, conn):
            self.conn = conn

        def list_items(self, user_id, *, include_archived=False, limit=200, messages_per_item=None):
            calls["list"] = (user_id, include_archived, limit, messages_per_item)
            return [{"id": "req_123", "type": "request", "title": "Existing task", "status": "working"}]

    def fake_sync(user_id, *, user_dir, task_status_file):
        calls["sync"] = (user_id, user_dir, task_status_file)

    monkeypatch.setattr(control_repository, "sync_user_from_legacy", fake_sync)
    monkeypatch.setattr(control_repository, "ControlRepository", FakeRepo)
    monkeypatch.setattr(control_db, "transaction", lambda: FakeTx())

    resp = client.get("/api/ang/threads?limit=10&messages_per_item=3")

    assert resp.status_code == 200
    assert resp.json()["threads"] == [
        {"id": "req_123", "type": "request", "title": "Existing task", "status": "working"}
    ]
    assert calls["sync"][0] == "ang"
    assert calls["list"] == ("ang", False, 10, 3)
    assert _command_files(tmp_path) == []


def test_tasks_write_endpoint_is_flagged_off_by_default(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "CONTROL_API_WRITES_ENABLED", False)

    resp = client.post("/api/ang/tasks", json={"title": "New task", "content": "do it"})

    assert resp.status_code == 409
    assert resp.json()["detail"] == "Control API writes are disabled"
    assert _command_files(tmp_path) == []


def test_tasks_write_endpoint_projects_to_db_and_exports_compat(monkeypatch, tmp_path: Path):
    import control.db as control_db
    import control.repository as control_repository

    client = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "CONTROL_API_WRITES_ENABLED", True)
    monkeypatch.setattr(server, "BRIDGE_COMPAT_EXPORT_ENABLED", True)
    calls = {}

    class FakeTx:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeRepo:
        def __init__(self, conn):
            self.conn = conn

        def create_task(self, **kwargs):
            calls["create_task"] = kwargs
            return {
                "id": kwargs["task_id"],
                "type": kwargs["item_type"],
                "title": kwargs["title"],
                "status": "queued",
                "tags": kwargs["tags"],
                "origin": "user",
                "pinned": False,
                "quick": kwargs["quick"],
                "parent_id": None,
                "created_at": kwargs["created_at"],
                "updated_at": kwargs["created_at"],
                "messages": [
                    {
                        "id": kwargs["message_id"],
                        "sender": kwargs["sender"],
                        "content": kwargs["content"],
                        "timestamp": kwargs["created_at"],
                        "kind": "text",
                    }
                ],
                "error": None,
                "result_path": None,
            }

    monkeypatch.setattr(control_repository, "ControlRepository", FakeRepo)
    monkeypatch.setattr(control_db, "transaction", lambda: FakeTx())

    resp = client.post(
        "/api/ang/tasks",
        json={"title": "New task", "content": "do it", "quick": True, "tags": ["api"], "client_request_id": "abc123"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["item_id"] == "req_abc123"
    assert calls["create_task"]["task_id"] == "req_abc123"
    assert calls["create_task"]["message_id"] == "abc123"
    commands = _command_files(tmp_path)
    assert len(commands) == 1
    cmd = json.loads(commands[0].read_text(encoding="utf-8"))
    assert cmd["type"] == "new_request"
    assert cmd["item_id"] == "req_abc123"
    item_path = tmp_path / "bridge" / "users" / "ang" / "items" / "req_abc123.json"
    assert item_path.exists()
    assert json.loads(item_path.read_text(encoding="utf-8"))["status"] == "queued"


def test_legacy_request_endpoint_uses_canonical_api_when_icloud_commands_disabled(monkeypatch, tmp_path: Path):
    import control.db as control_db
    import control.repository as control_repository

    client = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "CONTROL_API_WRITES_ENABLED", True)
    monkeypatch.setattr(server, "ICLOUD_COMMAND_FALLBACK_ENABLED", False)
    calls = {}

    class FakeTx:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeRepo:
        def __init__(self, conn):
            self.conn = conn

        def create_task(self, **kwargs):
            calls["create_task"] = kwargs
            return {
                "id": kwargs["task_id"],
                "type": "request",
                "title": kwargs["title"],
                "status": "queued",
                "tags": kwargs["tags"],
                "origin": "user",
                "pinned": False,
                "quick": kwargs["quick"],
                "parent_id": None,
                "created_at": kwargs["created_at"],
                "updated_at": kwargs["created_at"],
                "messages": [],
                "error": None,
                "result_path": None,
            }

    monkeypatch.setattr(control_repository, "ControlRepository", FakeRepo)
    monkeypatch.setattr(control_db, "transaction", lambda: FakeTx())

    resp = client.post("/api/ang/request", json={"title": "Old client", "content": "do it"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    assert calls["create_task"]["task_id"].startswith("req_")
    assert _command_files(tmp_path) == []


def test_task_pin_endpoint_updates_db_and_compat_item(monkeypatch, tmp_path: Path):
    import control.db as control_db
    import control.repository as control_repository

    client = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "CONTROL_API_WRITES_ENABLED", True)
    monkeypatch.setattr(server, "BRIDGE_COMPAT_EXPORT_ENABLED", True)

    class FakeTx:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeRepo:
        def __init__(self, conn):
            self.conn = conn

        def set_pinned(self, user_id, task_id, pinned):
            return {
                "id": task_id,
                "type": "request",
                "title": "Pinned",
                "status": "queued",
                "tags": [],
                "origin": "user",
                "pinned": pinned,
                "quick": False,
                "parent_id": None,
                "created_at": "2026-04-30T00:00:00Z",
                "updated_at": "2026-04-30T00:00:01Z",
                "messages": [],
                "error": None,
                "result_path": None,
            }

    monkeypatch.setattr(control_repository, "ControlRepository", FakeRepo)
    monkeypatch.setattr(control_db, "transaction", lambda: FakeTx())

    resp = client.post("/api/ang/tasks/req_123/pin", json={"pinned": True})

    assert resp.status_code == 200
    assert resp.json()["pinned"] is True
    compat_item = tmp_path / "bridge" / "users" / "ang" / "items" / "req_123.json"
    assert json.loads(compat_item.read_text(encoding="utf-8"))["pinned"] is True
    cmd = json.loads(_command_files(tmp_path)[0].read_text(encoding="utf-8"))
    assert cmd["type"] == "pin"
    assert cmd["pinned"] is True


def test_task_cancel_endpoint_updates_runtime_db_and_exports_compat(monkeypatch, tmp_path: Path):
    import control.db as control_db
    import control.repository as control_repository

    client = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "CONTROL_API_WRITES_ENABLED", True)
    monkeypatch.setattr(server, "BRIDGE_COMPAT_EXPORT_ENABLED", True)
    monkeypatch.setattr(
        server, "_task_manager", lambda: type("TM", (), {"cancel_task": lambda self, *a, **k: object()})()
    )

    class FakeTx:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeRepo:
        def __init__(self, conn):
            self.conn = conn

        def update_task_status(self, user_id, task_id, status, **kwargs):
            return {
                "id": task_id,
                "type": "request",
                "title": "Cancel me",
                "status": "failed",
                "tags": [],
                "origin": "user",
                "pinned": False,
                "quick": False,
                "parent_id": None,
                "created_at": "2026-04-30T00:00:00Z",
                "updated_at": "2026-04-30T00:00:01Z",
                "messages": [],
                "error": {"code": kwargs["error_code"], "message": kwargs["error_message"], "retryable": False},
                "result_path": None,
            }

    monkeypatch.setattr(control_repository, "ControlRepository", FakeRepo)
    monkeypatch.setattr(control_db, "transaction", lambda: FakeTx())

    resp = client.post("/api/ang/tasks/req_cancel/cancel")

    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    cmd = json.loads(_command_files(tmp_path)[0].read_text(encoding="utf-8"))
    assert cmd["type"] == "cancel"
    assert cmd["item_id"] == "req_cancel"


def test_task_retry_requires_runtime_db(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "CONTROL_API_WRITES_ENABLED", True)
    monkeypatch.setattr(server, "CONTROL_RUNTIME_DB_ENABLED", False)

    resp = client.post("/api/ang/tasks/req_retry/retry")

    assert resp.status_code == 409
    assert resp.json()["detail"] == "Control runtime DB dispatch is disabled"


def test_task_retry_endpoint_requeues_task(monkeypatch, tmp_path: Path):
    import control.db as control_db
    import control.repository as control_repository

    client = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "CONTROL_API_WRITES_ENABLED", True)
    monkeypatch.setattr(server, "CONTROL_RUNTIME_DB_ENABLED", True)
    calls = {}

    class FakeTaskManager:
        def reset_for_retry(self, task_id):
            calls["reset_for_retry"] = task_id
            return object()

    class FakeTx:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeRepo:
        def __init__(self, conn):
            self.conn = conn

        def update_task_status(self, user_id, task_id, status, **kwargs):
            calls["update_task_status"] = (user_id, task_id, status, kwargs)
            return {
                "id": task_id,
                "type": "request",
                "title": "Retry me",
                "status": "queued",
                "tags": [],
                "origin": "user",
                "pinned": False,
                "quick": False,
                "parent_id": None,
                "created_at": "2026-04-30T00:00:00Z",
                "updated_at": "2026-04-30T00:00:01Z",
                "messages": [],
                "error": None,
                "result_path": None,
            }

    monkeypatch.setattr(server, "_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(control_repository, "ControlRepository", FakeRepo)
    monkeypatch.setattr(control_db, "transaction", lambda: FakeTx())

    resp = client.post("/api/ang/tasks/req_retry/retry")

    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    assert calls["reset_for_retry"] == "req_retry"
    assert calls["update_task_status"][0:3] == ("ang", "req_retry", "queued")
    assert calls["update_task_status"][3]["summary"] == "Retry requested"


def test_v2_status_card_creates_app_visible_item(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path)

    resp = client.post(
        "/api/ang/v2-status/cards",
        json={
            "card_type": "decision",
            "title": "Cutover ready",
            "body": "Shadow passed. Reply GO / WAIT / ABORT.",
            "reply_options": ["GO", "WAIT", "ABORT"],
            "default_action": "WAIT",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    item = body["item"]
    assert body["status"] == "needs-input"
    assert item["type"] == "v2_status"
    assert item["channel"] == "v2_status"
    assert item["card_type"] == "decision"
    assert item["reply_options"] == ["GO", "WAIT", "ABORT"]
    item_path = tmp_path / "bridge" / "users" / "ang" / "items" / f"{body['item_id']}.json"
    assert item_path.exists()
    manifest = json.loads((tmp_path / "bridge" / "users" / "ang" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["items"][0]["id"] == body["item_id"]


def test_v2_status_reply_validates_options_and_records_command(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path)
    created = client.post(
        "/api/ang/v2-status/cards",
        json={
            "card_type": "decision",
            "title": "Cutover ready",
            "body": "Shadow passed.",
            "reply_options": ["GO", "WAIT", "ABORT"],
        },
    ).json()
    card_id = created["item_id"]

    bad = client.post(f"/api/ang/v2-status/cards/{card_id}/reply", json={"reply": "MAYBE"})
    assert bad.status_code == 422

    ok = client.post(f"/api/ang/v2-status/cards/{card_id}/reply", json={"reply": "go"})
    assert ok.status_code == 200
    item = ok.json()["item"]
    assert item["status"] == "done"
    assert item["messages"][-1]["kind"] == "v2_status_reply"
    assert item["messages"][-1]["content"] == "GO"
    cmd = json.loads(_command_files(tmp_path)[0].read_text(encoding="utf-8"))
    assert cmd["type"] == "v2_status_reply"
    assert cmd["reply"] == "GO"
    assert cmd["item_id"] == card_id


def test_start_mdns_advertisement_uses_mira_service(monkeypatch):
    calls = []

    class FakeProcess:
        def terminate(self):
            calls.append(("terminate",))

    monkeypatch.setattr(server, "_mdns_process", None)
    monkeypatch.setattr(server, "MDNS_ADVERTISE_ENABLED", True)
    monkeypatch.setattr(server.shutil, "which", lambda name: "/usr/bin/dns-sd" if name == "dns-sd" else None)
    monkeypatch.setattr(server.subprocess, "DEVNULL", object())
    monkeypatch.setattr(server.subprocess, "run", lambda *args, **kwargs: None)

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeProcess()

    monkeypatch.setattr(server.subprocess, "Popen", fake_popen)

    server._start_mdns_advertisement()

    assert calls[0][0][:5] == ["/usr/bin/dns-sd", "-R", "Mira", "_mira._tcp", "local"]
    assert str(server.WEBGUI_PORT) in calls[0][0]
    assert "path=/api/heartbeat" in calls[0][0]

    server._stop_mdns_advertisement()
    assert calls[-1] == ("terminate",)


def test_share_requires_existing_item_and_does_not_enqueue_command(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "ICLOUD_COMMAND_FALLBACK_ENABLED", True)

    resp = client.post("/api/ang/items/missing/share")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Item not found"
    assert _command_files(tmp_path) == []


def test_reply_enqueues_command_for_existing_item(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "ICLOUD_COMMAND_FALLBACK_ENABLED", True)
    item_path = tmp_path / "bridge" / "users" / "ang" / "items" / "req_123.json"
    item_path.write_text(
        json.dumps(
            {
                "id": "req_123",
                "title": "Existing item",
                "messages": [],
                "updated_at": "2026-04-05T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    resp = client.post("/api/ang/items/req_123/reply", json={"content": "hello"})

    assert resp.status_code == 200
    commands = _command_files(tmp_path)
    assert len(commands) == 1
    cmd = json.loads(commands[0].read_text(encoding="utf-8"))
    assert cmd["type"] == "reply"
    assert cmd["item_id"] == "req_123"


def test_operator_endpoint_prefers_cached_dashboard(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path)
    dashboard_path = tmp_path / "bridge" / "users" / "ang" / "operator" / "dashboard.json"
    dashboard_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard_path.write_text(json.dumps({"user_id": "ang", "tasks": {"active": []}}), encoding="utf-8")

    resp = client.get("/api/ang/operator")

    assert resp.status_code == 200
    assert resp.json()["user_id"] == "ang"
