from __future__ import annotations

import importlib.util
import json
from pathlib import Path

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


def test_web_api_requires_token_when_configured(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path, token="secret-token", allow_loopback=True)
    blocked = client.get("/api/ang/items")
    allowed = client.get("/api/ang/items", headers={"X-Mira-Token": "secret-token"})
    assert blocked.status_code == 401
    assert allowed.status_code == 200


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

    resp = client.post("/api/ang/items/missing/reply", json={"content": "hello"})

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Item not found"
    assert _command_files(tmp_path) == []


def test_share_requires_existing_item_and_does_not_enqueue_command(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path)

    resp = client.post("/api/ang/items/missing/share")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Item not found"
    assert _command_files(tmp_path) == []


def test_reply_enqueues_command_for_existing_item(monkeypatch, tmp_path: Path):
    client = _make_client(monkeypatch, tmp_path)
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
