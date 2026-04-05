from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


SERVER_PATH = Path(__file__).resolve().parents[3] / "web" / "server.py"
SPEC = importlib.util.spec_from_file_location("mira_web_server_for_tests", SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(server)


def _make_client(monkeypatch, tmp_path: Path, *, token: str = "", allow_loopback: bool = True, profiles: dict | None = None) -> TestClient:
    bridge = tmp_path / "bridge"
    users_dir = bridge / "users"
    (users_dir / "ang" / "items").mkdir(parents=True)
    (users_dir / "ang" / "commands").mkdir(parents=True)
    (users_dir / "liquan" / "items").mkdir(parents=True)
    bridge.mkdir(exist_ok=True)
    if profiles is not None:
        (bridge / "profiles.json").write_text(json.dumps(profiles), encoding="utf-8")

    monkeypatch.setattr(server, "BRIDGE", bridge)
    monkeypatch.setattr(server, "USERS_DIR", users_dir)
    monkeypatch.setattr(server, "WEBGUI_TOKEN", token)
    monkeypatch.setattr(server, "WEBGUI_ALLOW_LOOPBACK_WITHOUT_TOKEN", allow_loopback)
    monkeypatch.setattr(server, "get_known_user_ids", lambda: ["ang", "liquan"])
    monkeypatch.setattr(server, "is_known_user", lambda user_id: user_id in {"ang", "liquan"})
    monkeypatch.setattr(server, "get_user_config", lambda user_id: {"display_name": user_id.title()})
    return TestClient(server.app)


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
    assert resp.json() == {
        "profiles": [{"id": "ang", "display_name": "Ang", "agent_name": "Mira"}]
    }


def test_safe_join_rejects_parent_traversal(tmp_path: Path):
    base = tmp_path / "artifacts"
    base.mkdir()
    with pytest.raises(HTTPException):
        server._safe_join(base, "..")
