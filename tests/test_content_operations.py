"""Operational alerts: real SQLite and authenticated bridge, no model or cloud."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from obligations import Conflict, Ledger
from content_worker.operations import record_operation


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "state/ledger.sqlite3", tmp_path, clock=lambda: datetime(2026, 9, 24, tzinfo=timezone.utc))


def receipt(n=1, outcome="failed", reason="service_failed", unit="mira-creator.service"):
    return dict(
        unit=unit, run_id=f"{n:032x}", completed_at=f"2026-09-23T12:{n:02}:00+00:00", outcome=outcome, reason=reason
    )


def alerts(ledger):
    return [e for e in ledger.events()["items"] if e["kind"] != "operation.observed"]


def test_concurrent_replay_writes_one_failure(ledger):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: record_operation(ledger, receipt()), range(2)))
    assert results[0] == results[1]
    assert len(ledger.events()["items"]) == 2
    assert alerts(ledger)[0]["kind"] == "operation.failed"
    assert ledger.listing(status="queued")["items"] == []
    with pytest.raises(Conflict, match="receipt_conflict"):
        record_operation(ledger, receipt(outcome="produced", reason="artifact_recorded"))


def test_streak_survives_restart_and_alerts_once_until_reset(ledger):
    for n in range(1, 7):
        # Reopen the actual database each tick; never count repeated polling.
        reopened = Ledger(ledger.path, ledger.root, clock=ledger.clock)
        row = receipt(n, "empty", "no_output")
        assert record_operation(reopened, row)["empty_streak"] == n
        record_operation(reopened, row)
    assert len(alerts(ledger)) == 1
    assert alerts(ledger)[0]["payload"]["empty_streak"] == 4
    record_operation(ledger, receipt(7, "produced", "artifact_recorded"))
    for n in range(8, 12):
        record_operation(ledger, receipt(n, "empty", "no_output"))
    assert len(alerts(ledger)) == 2


def test_skip_alert_timer_failure_and_units_are_independent(ledger):
    record_operation(ledger, receipt(outcome="skipped", reason="outside_edition_window"))
    record_operation(ledger, receipt(unit="tetra-research-evening.timer"))
    assert [e["kind"] for e in alerts(ledger)] == ["operation.skipped", "operation.failed"]
    assert all(e["payload"]["empty_streak"] == 0 for e in alerts(ledger))


def test_late_input_rejected_but_old_replay_works(ledger):
    first = record_operation(ledger, receipt(2))
    record_operation(ledger, receipt(3))
    assert record_operation(ledger, receipt(2)) == first
    with pytest.raises(Conflict, match="out_of_order"):
        record_operation(ledger, receipt(1))
    with pytest.raises(Conflict, match="threshold_change"):
        record_operation(ledger, receipt(4), empty_threshold=5)
    assert len(ledger.events()["items"]) == 4


@pytest.mark.parametrize(
    "change",
    [
        {"raw_log": "private"},
        {"outcome": "accepted_by_smtp"},
        {"reason": "secret URL"},
        {"unit": "personal-health.service"},
        {"run_id": "new-random-id-each-poll"},
        {"completed_at": "2026-09-23T12:00:00"},
        {"completed_at": "2027-01-01T00:00:00Z"},
        {"reason": []},
    ],
)
def test_rejects_unsafe_or_ambiguous_receipts(ledger, change):
    with pytest.raises(ValueError):
        record_operation(ledger, receipt() | change)
    assert ledger.events()["items"] == []


def test_transaction_rolls_back_observation_if_alert_write_fails(ledger, monkeypatch):
    original = ledger._event

    def fail(db, identifier, kind, *args):
        if kind == "operation.failed":
            raise OSError("injected storage failure")
        return original(db, identifier, kind, *args)

    monkeypatch.setattr(ledger, "_event", fail)
    with pytest.raises(OSError):
        record_operation(ledger, receipt())
    assert ledger.events()["items"] == []
    assert ledger.listing()["items"] == []
    monkeypatch.setattr(ledger, "_event", original)
    assert record_operation(ledger, receipt())["alert_event_id"]


def test_failure_visible_through_existing_authenticated_cursor_api(ledger, tmp_path):
    spec = importlib.util.spec_from_file_location("operation_bridge", Path(__file__).parents[1] / "bridge/api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    # Match the existing API factory signature without a separate alert endpoint.
    token = "local-test-token-only"
    app = api.create_app(ledger, {"mira-app": token})
    client = TestClient(app)
    before = ledger.events()
    result = record_operation(ledger, receipt())
    response = client.get(
        "/events",
        params={"after": before["next_cursor"], "epoch": before["epoch"]},
        headers={"Authorization": "Bearer " + token},
    )
    assert response.status_code == 200
    assert result["alert_event_id"] in [e["event_id"] for e in response.json()["items"]]
    assert client.get("/events").status_code in {401, 403}
