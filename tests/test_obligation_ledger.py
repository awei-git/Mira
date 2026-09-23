"""Ledger/API/worker fault tests: local SQLite, virtual models, no AWS or publishing."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

from obligations import Conflict, Forbidden, Ledger, OWNERS
from content_worker.files import checksum
from content_worker.model_guard import guard_model_call, model_guard, PaidCallBlocked
from content_worker.seeds import POLICY_FILES, run_batch, run_queue, run_seed, seed_job

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ledger_api_under_test", ROOT / "bridge/api.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


@pytest.fixture
def fixture(tmp_path):
    repo = tmp_path / "repo"
    (repo / "seeds").mkdir(parents=True)
    (repo / "seeds/seeds.jsonl").write_text("")
    for name in POLICY_FILES:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Editorial policy fixture")
    clock = [datetime(2026, 9, 23, 12, tzinfo=timezone.utc)]
    ledger = Ledger(tmp_path / "state/ledger.sqlite3", repo, clock=lambda: clock[0])
    return repo, ledger, clock


def seed():
    return {"seed_id": "example", "status": "ready", "track": "substack_en"}


def running(fixture, lease_seconds=60):
    repo, ledger, clock = fixture
    job = seed_job(repo, seed(), ledger)
    claimed = ledger.claim(job["id"], "mira-aws", job["revision"], lease_seconds=lease_seconds)
    return ledger.transition(job["id"], "mira-aws", claimed["revision"], "running")


def writer(workspace, *_):
    (workspace / "output.md").write_text("# Test draft\n\n> Reader promise\n\nThis is a test fixture.")
    return "fixture only"


def reviewer(workspace, *_):
    return (workspace / "output.md").read_text(), {"pass_gate": True}


def test_idempotent_creation_and_conflicting_payload(fixture):
    repo, ledger, _ = fixture
    one = seed_job(repo, seed(), ledger)
    assert seed_job(repo, seed(), ledger) == one
    with pytest.raises(Conflict):
        ledger.create("agent", "mira-aws", {"different": True}, one["idempotency_key"], "mira-aws")
    assert len(ledger.listing()["items"]) == 1


def test_concurrent_claim_has_one_winner(fixture):
    repo, ledger, _ = fixture
    job = seed_job(repo, seed(), ledger)

    def attempt(_):
        try:
            return ledger.claim(job["id"], "mira-aws", 0)["status"]
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ["claimed", "conflict"]
    assert [e["kind"] for e in ledger.events()["items"]].count("obligation.claimed") == 1


def test_ownership_revision_and_false_completion_are_rejected(fixture):
    job = running(fixture)
    _, ledger, _ = fixture
    with pytest.raises(Forbidden):
        ledger.transition(job["id"], "codex", job["revision"], "blocked")
    with pytest.raises(Conflict):
        ledger.transition(job["id"], "mira-aws", 0, "blocked")
    with pytest.raises(Forbidden, match="artifact"):
        ledger.transition(job["id"], "mira-aws", job["revision"], "succeeded")
    with pytest.raises(Forbidden):
        ledger.create("publication.authorized", "mira-aws", {}, "cannot-mint", "mira-app")


def test_cursor_replay_epoch_and_append_only_history(fixture):
    repo, ledger, _ = fixture
    seed_job(repo, seed(), ledger)
    first = ledger.events(limit=1)
    assert ledger.events(limit=1) == first
    assert ledger.events(after=first["next_cursor"], epoch=first["epoch"])["items"] == []
    with pytest.raises(Conflict, match="epoch"):
        ledger.events(epoch="replaced-database")
    db = ledger.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            db.execute("DELETE FROM obligation_events")
    finally:
        db.close()


def test_expiry_without_paid_attempt_can_requeue_but_live_lease_cannot(fixture):
    job = running(fixture)
    _, ledger, clock = fixture
    with pytest.raises(Conflict, match="still_live"):
        ledger.reconcile(job["id"], "mira-aws", job["revision"])
    clock[0] += timedelta(minutes=2)
    recovered = ledger.reconcile(job["id"], "mira-aws", job["revision"])
    assert recovered["status"] == "queued"
    reclaimed = ledger.claim(job["id"], "mira-aws", recovered["revision"])
    assert reclaimed["revision"] > job["revision"]
    with pytest.raises(Conflict):
        ledger.transition(job["id"], "mira-aws", job["revision"], "running")


def test_uncertain_paid_step_blocks_reclaim_even_when_receipt_disappears(fixture):
    job = running(fixture)
    repo, ledger, clock = fixture
    calls = []

    @guard_model_call
    def timeout(*args):
        calls.append(1)
        raise TimeoutError("provider may have billed")

    with pytest.raises(TimeoutError):
        with model_guard(ledger, job, repo):
            timeout("test-route", "prompt", "", 1)
    receipt = next((repo / job["payload"]["draft_dir"] / "steps").glob("*/receipt.json"))
    receipt.unlink()  # Simulated lost response/reservation storage.
    clock[0] += timedelta(hours=4)
    blocked = ledger.reconcile(job["id"], "mira-aws", job["revision"])
    assert blocked["status"] == "blocked" and len(calls) == 1
    assert blocked["paid_started"] == 1
    with pytest.raises(Conflict):
        ledger.claim(job["id"], "mira-aws", blocked["revision"])


def test_reservation_precedes_dispatch_and_success_is_durable(fixture):
    job = running(fixture)
    repo, ledger, _ = fixture

    @guard_model_call
    def fake_provider(*args):
        events = ledger.events()["items"]
        started = next(e for e in events if e["kind"] == "model_step.started")
        assert (repo / started["payload"]["receipt_path"]).is_file()
        return "Model response fixture"

    with model_guard(ledger, job, repo):
        assert fake_provider("test-route", "prompt", "", 1) == "Model response fixture"
    assert ledger.events()["items"][-1]["kind"] == "model_step.completed"


def test_worker_threads_share_guard_and_failure_prevents_more_paid_calls(fixture):
    job = running(fixture)
    repo, ledger, _ = fixture

    @guard_model_call
    def fake_provider(*args):
        return "response"

    with model_guard(ledger, job, repo):
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(lambda n: fake_provider("test-route", str(n), "", 1), range(2))) == [
                "response",
                "response",
            ]
    assert len([e for e in ledger.events()["items"] if e["kind"] == "model_step.started"]) == 2


def test_draft_event_recovery_does_not_rerun_model(fixture, monkeypatch):
    repo, ledger, _ = fixture
    calls = []

    def counted(*args):
        calls.append(1)
        return writer(*args)

    complete = ledger.complete_draft

    def fail_once(*args):
        raise sqlite3.OperationalError("injected commit outage")

    monkeypatch.setattr(ledger, "complete_draft", fail_once)
    first = run_seed(repo, seed(), writer=counted, reviewer=reviewer, ledger=ledger)
    assert first["status"] == "awaiting_ledger_event"
    assert first["ledger_event_written"] is False
    assert not [e for e in ledger.events()["items"] if e["kind"] == "draft.ready_for_signoff"]
    monkeypatch.setattr(ledger, "complete_draft", complete)
    second = run_batch(repo, [seed()], writer=counted, reviewer=reviewer, ledger=ledger)[0]
    assert second["ledger_event_written"] is True and len(calls) == 1
    assert len([e for e in ledger.events()["items"] if e["kind"] == "draft.ready_for_signoff"]) == 1
    assert second["published"] is False


def test_expired_completed_draft_only_replays_event(fixture, monkeypatch):
    repo, ledger, clock = fixture
    complete = ledger.complete_draft
    monkeypatch.setattr(ledger, "complete_draft", lambda *a: (_ for _ in ()).throw(Conflict("injected")))
    first = run_seed(repo, seed(), writer=writer, reviewer=reviewer, ledger=ledger)
    clock[0] += timedelta(hours=4)
    monkeypatch.setattr(ledger, "complete_draft", complete)
    result = ledger.reconcile(first["obligation_id"], "mira-aws", ledger.get(first["obligation_id"])["revision"])
    assert result["status"] == "succeeded"
    assert result["result"]["event_id"]


def test_packet_tamper_and_symlinks_cannot_be_registered(fixture):
    repo, ledger, _ = fixture
    done = run_seed(repo, seed(), writer=writer, reviewer=reviewer, ledger=ledger)
    job = ledger.get(done["obligation_id"])
    artifact_id = job["result"]["artifact_id"]
    path = repo / done["draft_path"]
    path.write_text("changed text")
    with pytest.raises(Conflict):
        ledger.artifact(artifact_id)
    path.unlink()
    path.symlink_to(repo / "seeds/seeds.jsonl")
    with pytest.raises(ValueError, match="symlink"):
        ledger.artifact(artifact_id)


def test_editorial_failure_writes_no_signoff_event(fixture):
    repo, ledger, _ = fixture
    result = run_seed(repo, seed(), writer=writer, reviewer=lambda *a: ("draft", {"pass_gate": False}), ledger=ledger)
    assert result["status"] == "editorial_blocked"
    assert ledger.get(result["obligation_id"])["status"] == "blocked"
    assert not [e for e in ledger.events()["items"] if e["kind"] == "draft.ready_for_signoff"]


def test_completed_version_cannot_be_replaced_by_consistently_changed_packet(fixture):
    repo, ledger, _ = fixture
    done = run_seed(repo, seed(), writer=writer, reviewer=reviewer, ledger=ledger)
    job = ledger.get(done["obligation_id"])
    draft = repo / done["draft_path"]
    draft.write_text("A different version, after signoff event creation.")
    packet_path = repo / job["payload"]["draft_dir"] / "packet.json"
    packet = json.loads(packet_path.read_text())
    packet["draft_sha256"] = checksum(draft.read_bytes())
    packet_path.write_text(json.dumps(packet))
    with pytest.raises(Conflict, match="version_changed"):
        ledger.complete_draft(job["id"], "mira-aws", job["revision"])
    assert ledger.get(job["id"])["result"]["draft_sha256"] == done["draft_sha256"]
    assert len([e for e in ledger.events()["items"] if e["kind"] == "draft.ready_for_signoff"]) == 1


def test_legacy_import_preserves_source_and_stable_id(fixture, tmp_path):
    _, ledger, _ = fixture
    task = {
        "id": "old123",
        "kind": "agent",
        "title": "old task",
        "body": "content only",
        "payload": {},
        "status": "done",
        "result": "old output",
    }
    path = tmp_path / "old123.json"
    path.write_text(json.dumps(task))
    raw = path.read_bytes()
    first = ledger.import_legacy(path)
    assert ledger.import_legacy(path) == first
    assert path.read_bytes() == raw
    assert api.legacy_view(first)["status"] == "done"
    task["result"] = "new output"
    path.write_text(json.dumps(task))
    with pytest.raises(Conflict):
        ledger.import_legacy(path)


def test_backup_preserves_epoch_and_events(fixture, tmp_path):
    repo, ledger, _ = fixture
    seed_job(repo, seed(), ledger)
    destination = tmp_path / "backup.sqlite3"
    ledger.backup(destination)
    restored = Ledger(destination, repo)
    assert restored.events() == ledger.events()
    with pytest.raises(Conflict):
        ledger.backup(destination)


def test_api_auth_role_fencing_event_replay_and_artifact_hash(fixture):
    repo, ledger, _ = fixture
    tokens = {name: uuid.uuid4().hex for name in OWNERS}
    client = TestClient(api.create_app(ledger, tokens))
    assert client.get("/bridge/events").status_code == 401
    app_header = {"X-Bridge-Token": tokens["mira-app"]}
    worker_header = {"X-Bridge-Token": tokens["mira-aws"]}
    job = seed_job(repo, seed(), ledger)
    assert (
        client.post(f"/obligations/{job['id']}/claim", headers=app_header, json={"expected_revision": 0}).status_code
        == 403
    )
    assert (
        client.post(
            "/obligations",
            headers=worker_header,
            json={"kind": "publication.authorized", "owner": "mira-aws", "payload": {}, "idempotency_key": "bad"},
        ).status_code
        == 403
    )
    assert client.post("/human-approvals", headers=app_header, json={}).status_code == 404
    done = run_seed(repo, seed(), writer=writer, reviewer=reviewer, ledger=ledger)
    page = client.get("/bridge/events", headers=app_header).json()
    event = next(e for e in page["items"] if e["kind"] == "draft.ready_for_signoff")
    assert event["payload"]["track"] == "substack_en"
    assert event["payload"]["kind"] == "essay"
    result = client.get("/artifacts/" + event["payload"]["artifact_id"], headers=app_header)
    assert result.status_code == 200
    assert checksum(result.content) == result.headers["x-content-sha256"] == done["draft_sha256"]
    assert (
        client.get("/events", params={"after": page["next_cursor"], "epoch": page["epoch"]}, headers=app_header).json()[
            "items"
        ]
        == []
    )
    assert client.get("/events", params={"epoch": "wrong"}, headers=app_header).status_code == 409
    assert client.post("/v1/tasks", headers=app_header, content="x" * 70_000).status_code == 413


def test_legacy_api_uses_same_ledger_and_no_second_queue(fixture):
    repo, ledger, _ = fixture
    token = uuid.uuid4().hex
    client = TestClient(api.create_app(ledger, {"codex": token}))
    headers = {"Authorization": "Bearer " + token, "Idempotency-Key": "repeat-ping"}
    first = client.post("/v1/tasks", headers=headers, json={"kind": "ping"}).json()
    assert client.post("/v1/tasks", headers=headers, json={"kind": "ping"}).json() == first
    assert first["result"] == "pong" and first["status"] == "done"
    assert ledger.get(first["id"])["status"] == "succeeded"
    assert client.get("/v1/tasks/" + first["id"], headers=headers).json()["result"] == "pong"
    assert not (repo / "tasks").exists()


def test_queue_only_runs_registered_exact_deployed_version(fixture):
    repo, ledger, _ = fixture
    (repo / "seeds/seeds.jsonl").write_text(json.dumps(seed()))
    assert run_queue(repo, ledger=ledger, writer=writer, reviewer=reviewer) == []
    job = seed_job(repo, seed(), ledger)
    assert run_queue(repo, ledger=ledger, writer=writer, reviewer=reviewer)[0]["obligation_id"] == job["id"]
    assert run_queue(repo, ledger=ledger, writer=writer, reviewer=reviewer) == []


def test_queued_old_version_is_not_replaced_by_new_source(fixture):
    repo, ledger, _ = fixture
    job = seed_job(repo, seed(), ledger)
    (repo / "seeds/seeds.jsonl").write_text(json.dumps({**seed(), "title": "Changed after assignment"}))
    assert run_queue(repo, ledger=ledger, writer=writer, reviewer=reviewer) == []
    assert ledger.get(job["id"])["status"] == "queued"


def test_guard_failure_disallows_fallback_but_legacy_route_is_unchanged(fixture, monkeypatch):
    import llm

    repo, ledger, _ = fixture
    calls = []
    monkeypatch.setattr(llm, "_fallback_chain", lambda *a: ["first", "second"])

    def dispatch(name, *args):
        calls.append(name)
        if name == "first":
            raise TimeoutError("uncertain provider outcome")
        return "legacy fallback output"

    monkeypatch.setattr(llm, "_call_think_model", guard_model_call(dispatch))
    assert llm._think_with_fallbacks("fixture", model_name="first") == "legacy fallback output"
    assert calls == ["first", "second"]
    calls.clear()
    job = running(fixture)
    with pytest.raises(TimeoutError):
        with model_guard(ledger, job, repo):
            llm._think_with_fallbacks("fixture", model_name="first")
    assert calls == ["first"]


def test_guard_stops_new_dispatch_after_caught_failure(fixture):
    repo, ledger, _ = fixture
    job = running(fixture)
    calls = []

    @guard_model_call
    def dispatch(*args):
        calls.append(1)
        raise TimeoutError("uncertain")

    with pytest.raises(PaidCallBlocked):
        with model_guard(ledger, job, repo):
            with pytest.raises(TimeoutError):
                dispatch("test", "prompt", "", 1)
            with pytest.raises(PaidCallBlocked):
                dispatch("test", "another prompt", "", 1)
    assert len(calls) == 1
