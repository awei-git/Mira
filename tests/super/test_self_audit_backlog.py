from __future__ import annotations

from pathlib import Path


def test_build_backlog_record_has_stable_id_and_review_metadata():
    import self_audit

    finding = {
        "type": "pipeline_error",
        "severity": "critical",
        "description": "Article 'Demo' has error: publish failed",
    }

    first = self_audit.build_backlog_record(finding, audit_date="2026-05-02")
    second = self_audit.build_backlog_record(dict(finding), audit_date="2026-05-03")

    assert first["item_id"] == second["item_id"]
    assert first["kind"] == "self_audit_finding"
    assert first["executor"] == "manual_review.required"
    assert first["priority"] == "high"
    assert first["payload"]["severity"] == "critical"
    assert first["payload"]["owner"] == "publishing"
    assert first["payload"]["executor_eligible"] is False
    assert first["payload"]["verification_criteria"]


def test_low_risk_backlog_record_is_executor_eligible():
    import self_audit

    original_agents_dir = self_audit._AGENTS_DIR
    tmp_agents_dir = Path(__file__).parent
    self_audit._AGENTS_DIR = tmp_agents_dir
    target = tmp_agents_dir / "photo_handler_fixture.py"
    target.write_text('from pathlib import Path\nROOT = Path.home() / "Sandbox/Mira/artifacts"\n')
    try:
        record = self_audit.build_backlog_record(
            {
                "type": "anti_pattern",
                "severity": "info",
                "pattern_name": "hardcoded_path",
                "description": "Hardcoded path",
                "file": target.name,
                "line": 10,
                "match": "Path.home()",
            },
            audit_date="2026-05-02",
        )
    finally:
        target.unlink(missing_ok=True)
        self_audit._AGENTS_DIR = original_agents_dir

    assert record["executor"] == "self_audit.apply_low_risk"
    assert record["payload"]["executor_eligible"] is True
    assert record["priority"] == "low"


def test_path_home_without_supported_replacement_requires_manual_review():
    import self_audit

    record = self_audit.build_backlog_record(
        {
            "type": "anti_pattern",
            "severity": "info",
            "pattern_name": "hardcoded_path",
            "description": "Hardcoded path",
            "file": "photo/handler.py",
            "line": 10,
            "match": "Path.home()",
        },
        audit_date="2026-05-02",
    )

    assert record["executor"] == "manual_review.required"
    assert record["payload"]["executor_eligible"] is False


def test_unimplemented_low_risk_pattern_requires_manual_review():
    import self_audit

    record = self_audit.build_backlog_record(
        {
            "type": "anti_pattern",
            "severity": "info",
            "pattern_name": "hardcoded_path",
            "description": "Hardcoded path",
            "file": "photo/handler.py",
            "line": 10,
            "match": '"/Users/',
        },
        audit_date="2026-05-02",
    )

    assert record["executor"] == "manual_review.required"
    assert record["payload"]["executor_eligible"] is False


def test_backlog_id_keeps_distinct_line_findings_separate():
    import self_audit

    base = {
        "type": "anti_pattern",
        "severity": "info",
        "pattern_name": "hardcoded_path",
        "description": "Hardcoded path",
        "file": "photo/handler.py",
    }

    first = self_audit.build_backlog_record({**base, "line": 10}, audit_date="2026-05-02")
    second = self_audit.build_backlog_record({**base, "line": 20}, audit_date="2026-05-02")

    assert first["item_id"] != second["item_id"]


def test_backlog_id_keeps_distinct_same_line_matches_separate():
    import self_audit

    base = {
        "type": "anti_pattern",
        "severity": "info",
        "pattern_name": "hardcoded_path",
        "description": "Hardcoded path",
        "file": "super/self_audit.py",
        "line": 157,
    }

    first = self_audit.build_backlog_record({**base, "match": '"/Users/'}, audit_date="2026-05-02")
    second = self_audit.build_backlog_record({**base, "match": "~/Sandbox"}, audit_date="2026-05-02")

    assert first["item_id"] != second["item_id"]


def test_upsert_self_audit_backlog_uses_control_repository(monkeypatch):
    import self_audit

    calls = []

    class FakeRepo:
        def __init__(self, conn):
            self.conn = conn

        def upsert_backlog_item(self, **kwargs):
            calls.append(kwargs)
            return {"id": kwargs["item_id"], "status": kwargs["status"]}

    class FakeTransaction:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(self_audit, "transaction", None, raising=False)
    monkeypatch.setitem(
        __import__("sys").modules,
        "control.db",
        type("FakeDb", (), {"transaction": lambda: FakeTransaction()}),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "control.repository",
        type("FakeRepositoryModule", (), {"ControlRepository": FakeRepo}),
    )

    records = self_audit.upsert_self_audit_backlog(
        [{"type": "recurring_error", "severity": "warning", "pattern": "same failure"}],
        user_id="default",
    )

    assert len(records) == 1
    assert calls[0]["user_id"] == "default"
    assert calls[0]["kind"] == "self_audit_finding"
    assert calls[0]["payload"]["source"] == "self_audit"


def test_runtime_health_scan_turns_active_failures_into_findings(monkeypatch):
    import operator_dashboard as od
    import self_audit

    monkeypatch.setattr(
        od,
        "_load_bg_health",
        lambda: {
            "processes": [
                {
                    "name": "book-review",
                    "consecutive_failures": 4,
                    "last_failure_reason": "short output",
                }
            ]
        },
    )
    monkeypatch.setattr(od, "_process_has_active_failure", lambda proc: True)
    monkeypatch.setattr(
        od,
        "_recent_incidents",
        lambda: [
            {
                "pipeline": "publish",
                "step": "substack_publish",
                "error_type": "timeout",
                "error_message": "timed out",
                "count": 3,
                "timestamp": "recent",
            }
        ],
    )
    monkeypatch.setattr(od, "_is_recent_iso", lambda value, hours: value == "recent")

    findings = self_audit.scan_runtime_health()

    assert {finding["type"] for finding in findings} == {
        "scheduled_process_failure",
        "repeated_pipeline_incident",
    }
    assert all(finding["severity"] == "critical" for finding in findings)


def test_integration_config_scan_reports_missing_bluesky(monkeypatch):
    import bluesky.client as bluesky_client
    import self_audit

    monkeypatch.setattr(bluesky_client, "is_configured", lambda: False)

    findings = self_audit.scan_integration_config()

    assert findings == [
        {
            "type": "integration_config_missing",
            "severity": "warning",
            "description": (
                "Bluesky integration is enabled in social workflows but cannot authenticate: "
                "missing api_keys.bluesky.handle/app_password or reusable session cache"
            ),
            "integration": "bluesky",
        }
    ]


def test_manifest_scan_ignores_hidden_runtime_directories(tmp_path, monkeypatch):
    import self_audit

    agents_dir = tmp_path / "agents"
    (agents_dir / ".bg_pids").mkdir(parents=True)
    real_agent = agents_dir / "demo"
    real_agent.mkdir()
    (real_agent / "manifest.json").write_text(
        '{"name":"demo","description":"Demo","entry_point":"handler.py:handle"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(self_audit, "_AGENTS_DIR", agents_dir)

    assert self_audit.check_manifests() == []


def test_publish_manifest_error_classifier_separates_parked_and_active_states():
    import self_audit

    assert (
        self_audit._publish_manifest_error_finding(  # noqa: SLF001
            "skipped",
            {"status": "skip", "error": "old config error"},
        )
        is None
    )

    parked = self_audit._publish_manifest_error_finding(  # noqa: SLF001
        "blocked",
        {"status": "blocked_writer_gate", "title": "Needs Gate", "error": "writer gate missing"},
    )
    stale = self_audit._publish_manifest_error_finding(  # noqa: SLF001
        "published",
        {
            "status": "published",
            "title": "Already Live",
            "substack_url": "https://example.substack.com/p/already-live",
            "error": "old transient error",
        },
    )
    active = self_audit._publish_manifest_error_finding(  # noqa: SLF001
        "active",
        {"status": "blocked_publish_error", "title": "Needs Review", "error": "missing published URL"},
    )

    assert parked["type"] == "parked_publish_item"
    assert parked["severity"] == "warning"
    assert stale["type"] == "stale_publish_manifest_error"
    assert stale["severity"] == "warning"
    assert active["type"] == "pipeline_error"
    assert active["severity"] == "critical"


def test_execute_self_audit_low_risk_rejects_non_mechanical():
    import backlog_executor

    result = backlog_executor._execute_self_audit_low_risk(  # noqa: SLF001
        {"payload": {"finding": {"type": "pipeline_error", "severity": "critical"}}}
    )

    assert result["success"] is False
    assert "no implemented automatic fix" in result["reason"]
