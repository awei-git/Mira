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
        user_id="ang",
    )

    assert len(records) == 1
    assert calls[0]["user_id"] == "ang"
    assert calls[0]["kind"] == "self_audit_finding"
    assert calls[0]["payload"]["source"] == "self_audit"


def test_execute_self_audit_low_risk_rejects_non_mechanical():
    import backlog_executor

    result = backlog_executor._execute_self_audit_low_risk(  # noqa: SLF001
        {"payload": {"finding": {"type": "pipeline_error", "severity": "critical"}}}
    )

    assert result["success"] is False
    assert "no implemented automatic fix" in result["reason"]
