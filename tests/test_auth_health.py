from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime


def test_record_auth_event_writes_provider_state(monkeypatch, tmp_path):
    import auth_health
    import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "MIRA_DIR", tmp_path / "bridge")
    monkeypatch.setattr(config, "get_known_user_ids", lambda: ["ang"])

    auth_health.record_auth_event("anthropic_oauth", "oauth_auth_failure", status="failed", detail="rate limit")

    state = json.loads((tmp_path / "auth_state" / "anthropic_oauth.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert state["event"] == "oauth_auth_failure"
    assert (tmp_path / "auth_state" / "events.jsonl").exists()
    item = json.loads(
        (tmp_path / "bridge" / "users" / "ang" / "items" / "auth_alert_anthropic_oauth.json").read_text(
            encoding="utf-8"
        )
    )
    assert item["status"] == "needs-input"
    assert item["tags"][:2] == ["auth_alert", "anthropic_oauth"]
    assert (tmp_path / "bridge" / "users" / "ang" / "manifest.json").exists()


def test_run_auth_health_resolves_existing_alert(monkeypatch, tmp_path):
    import auth_health
    import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "MIRA_DIR", tmp_path / "bridge")
    monkeypatch.setattr(config, "get_known_user_ids", lambda: ["ang"])
    monkeypatch.setattr(
        auth_health,
        "check_anthropic_oauth",
        lambda: auth_health.AuthHealthResult("anthropic_oauth", "ok", "info", "ok"),
    )
    monkeypatch.setattr(
        auth_health,
        "check_bridge_tls_cert",
        lambda: auth_health.AuthHealthResult("bridge_tls_cert", "ok", "info", "ok"),
    )

    auth_health.record_auth_event("anthropic_oauth", "oauth_auth_failure", status="failed", detail="rate limit")
    auth_health.run_auth_health_checks()

    item = json.loads(
        (tmp_path / "bridge" / "users" / "ang" / "items" / "auth_alert_anthropic_oauth.json").read_text(
            encoding="utf-8"
        )
    )
    assert item["status"] == "done"
    assert item["auth_status"] == "ok"


def test_run_auth_health_checks_does_not_check_anthropic_api(monkeypatch, tmp_path):
    import auth_health
    import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "MIRA_DIR", tmp_path / "bridge")
    monkeypatch.setattr(config, "get_known_user_ids", lambda: [])
    monkeypatch.setattr(
        auth_health,
        "check_anthropic_oauth",
        lambda: auth_health.AuthHealthResult("anthropic_oauth", "ok", "info", "ok"),
    )
    monkeypatch.setattr(
        auth_health,
        "check_bridge_tls_cert",
        lambda: auth_health.AuthHealthResult("bridge_tls_cert", "ok", "info", "ok"),
    )

    results = auth_health.run_auth_health_checks()

    assert [result.provider for result in results] == ["anthropic_oauth", "bridge_tls_cert"]
    assert not (tmp_path / "data" / "auth_state" / "anthropic_api.json").exists()


def test_bridge_tls_cert_warns_when_expiring(monkeypatch, tmp_path):
    import auth_health

    cert = tmp_path / "server.crt"
    cert.write_text("fake", encoding="utf-8")
    expires = datetime.now(timezone.utc) + timedelta(days=20)

    monkeypatch.setattr(
        auth_health.ssl._ssl,
        "_test_decode_cert",
        lambda path: {"notAfter": format_datetime(expires, usegmt=True)},
    )

    result = auth_health.check_bridge_tls_cert(cert)

    assert result.provider == "bridge_tls_cert"
    assert result.status == "expiring"
    assert result.severity == "warning"
    assert result.days_remaining is not None and result.days_remaining < 30


def test_auth_or_quota_failure_detection():
    import auth_health

    assert auth_health.is_auth_or_quota_failure(RuntimeError("OAuth token expired"))
    assert auth_health.is_auth_or_quota_failure("429 rate limit")
    assert not auth_health.is_auth_or_quota_failure(RuntimeError("json parse failed"))
