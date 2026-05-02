from __future__ import annotations


class FakeAuditCursor:
    def __init__(self):
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        return (42,)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeUpsertCursor(FakeAuditCursor):
    def __init__(self, existing_updated_at=None):
        super().__init__()
        self.existing_updated_at = existing_updated_at

    def fetchone(self):
        if self.existing_updated_at is None:
            return None
        return (self.existing_updated_at, "queued", "user")


class FakeConn:
    def __init__(self):
        self.cursor_obj = FakeAuditCursor()

    def cursor(self):
        return self.cursor_obj


class FakeDictCursor:
    def __init__(self, *, one=None, many=None):
        self.one = list(one or [])
        self.many = list(many or [])
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        if not self.one:
            return None
        return self.one.pop(0)

    def fetchall(self):
        return self.many

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_enqueue_request_verify_marks_verified_when_task_verifier_passed(monkeypatch):
    import control.repository as repository
    from control.repository import ControlRepository

    row = {
        "id": "request_verify:task_verified",
        "user_id": "ang",
        "task_id": "task_verified",
        "kind": "request_verify",
        "executor": "request_verify.apply",
        "status": "verified",
        "priority": "medium",
        "payload": {
            "task_type": "file_artifact",
            "verification": {"verified": True},
        },
    }
    cursor = FakeDictCursor(one=[row])
    monkeypatch.setattr(repository, "dict_cursor", lambda conn: cursor)

    repo = ControlRepository(FakeConn())
    item = repo.enqueue_request_verify(
        {
            "id": "task_verified",
            "user_id": "ang",
            "status": "verified",
            "title": "Build the report",
            "task_type": "file_artifact",
            "outcome_verified": True,
            "verification": {
                "verified": True,
                "summary": "output.md exists",
                "expected_observable_outcome": "requested artifact exists",
            },
        }
    )

    assert item == row
    assert cursor.queries[0][1]["status"] == "verified"
    assert cursor.queries[0][1]["verification_summary"] == "output.md exists"


def test_enqueue_request_verify_leaves_unverified_task_in_backlog(monkeypatch):
    import control.repository as repository
    from control.repository import ControlRepository

    cursor = FakeDictCursor(one=[{"id": "request_verify:task_unverified", "status": "proposed"}])
    monkeypatch.setattr(repository, "dict_cursor", lambda conn: cursor)

    repo = ControlRepository(FakeConn())
    repo.enqueue_request_verify(
        {
            "id": "task_unverified",
            "user_id": "ang",
            "status": "completed_unverified",
            "title": "Draft something",
            "outcome_verified": False,
            "verification": {"verified": False, "summary": "semantic intent not checked"},
        }
    )

    params = cursor.queries[0][1]
    assert params["status"] == "proposed"
    assert params["priority"] == "high"
    assert params["last_error"] == "semantic intent not checked"


def test_claim_backlog_item(monkeypatch):
    import control.repository as repository
    from control.repository import ControlRepository

    claimed = {"id": "request_verify:t1", "task_id": "t1", "user_id": "ang", "status": "in_progress"}
    cursor = FakeDictCursor(
        one=[
            {"id": "request_verify:t1"},
            claimed,
        ]
    )
    monkeypatch.setattr(repository, "dict_cursor", lambda conn: cursor)

    repo = ControlRepository(FakeConn())
    assert repo.claim_backlog_item("request_verify.apply") == claimed
    assert "FOR UPDATE SKIP LOCKED" in cursor.queries[0][0]


def test_claim_task_for_dispatch_is_atomic(monkeypatch):
    from control.repository import ControlRepository

    events = []
    conn = FakeConn()
    repo = ControlRepository(conn)
    monkeypatch.setattr(repo, "_record_event", lambda *args, **kwargs: events.append((args, kwargs)))

    assert repo.claim_task_for_dispatch("ang", "req_1") is True

    query, params = conn.cursor_obj.queries[0]
    assert "status = 'dispatched'" in query
    assert "AND status = 'queued'" in query
    assert "AND origin = 'user'" in query
    assert params[-2:] == ("req_1", "ang")
    assert events[0][0][:3] == ("req_1", "ang", "task.dispatch_claimed")


def test_release_dispatch_claim_only_requeues_unstarted_claim(monkeypatch):
    from control.repository import ControlRepository

    events = []
    conn = FakeConn()
    repo = ControlRepository(conn)
    monkeypatch.setattr(repo, "_record_event", lambda *args, **kwargs: events.append((args, kwargs)))

    repo.release_dispatch_claim("ang", "req_1", reason="spawn failed")

    query, params = conn.cursor_obj.queries[0]
    assert "status = 'queued'" in query
    assert "status = 'dispatched'" in query
    assert "worker_pid IS NULL" in query
    assert params[-2:] == ("req_1", "ang")
    assert events[0][0][:3] == ("req_1", "ang", "task.dispatch_claim_released")
    assert events[0][1]["payload"] == {"reason": "spawn failed"}


def test_append_user_reply_requeues_completed_or_feed_item(monkeypatch):
    from control.repository import ControlRepository

    events = []
    conn = FakeConn()
    repo = ControlRepository(conn)
    monkeypatch.setattr(repo, "_record_event", lambda *args, **kwargs: events.append((args, kwargs)))
    monkeypatch.setattr(repo, "get_item", lambda user_id, task_id: {"id": task_id, "status": "queued"})

    item = repo.append_user_reply(
        user_id="ang",
        task_id="feed_market_1",
        message_id="msg_1",
        sender="ang",
        content="answer this",
        created_at="2026-05-02T13:21:06Z",
    )

    update_query = conn.cursor_obj.queries[2][0]
    assert "ELSE 'queued'" in update_query
    assert "origin = 'user'" in update_query
    assert item == {"id": "feed_market_1", "status": "queued"}
    assert events[0][0][:3] == ("feed_market_1", "ang", "message.created")


def test_upsert_agent_feed_defaults_to_done():
    from control.repository import ControlRepository

    conn = FakeConn()
    conn.cursor_obj = FakeUpsertCursor()
    repo = ControlRepository(conn)

    repo.upsert_bridge_item(
        "ang",
        {
            "id": "feed_report_1",
            "type": "feed",
            "title": "Report",
            "origin": "agent",
            "status": "queued",
            "messages": [],
        },
    )

    params = next(params for query, params in conn.cursor_obj.queries if "INSERT INTO" in query)
    assert params["status"] == "done"


def test_upsert_agent_feed_replaces_stale_generated_messages():
    from control.repository import ControlRepository

    conn = FakeConn()
    conn.cursor_obj = FakeUpsertCursor()
    repo = ControlRepository(conn)

    repo.upsert_bridge_item(
        "ang",
        {
            "id": "feed_zhesi_20260502",
            "type": "feed",
            "title": "Daily Zhesi",
            "origin": "agent",
            "status": "done",
            "messages": [
                {"id": "latest_a", "sender": "agent", "content": "current"},
                {"id": "latest_b", "sender": "agent", "content": "current follow-up"},
            ],
        },
    )

    delete_query, delete_params = next(
        (query, params) for query, params in conn.cursor_obj.queries if "DELETE FROM mira_control.messages" in query
    )
    assert "sender <> %s" in delete_query
    assert "id = ANY(%s)" in delete_query
    assert delete_params == ("feed_zhesi_20260502", "ang", "ang", ["latest_a", "latest_b"])


def test_upsert_discussion_preserves_message_history():
    from control.repository import ControlRepository

    conn = FakeConn()
    conn.cursor_obj = FakeUpsertCursor()
    repo = ControlRepository(conn)

    repo.upsert_bridge_item(
        "ang",
        {
            "id": "discussion_1",
            "type": "discussion",
            "title": "Discussion",
            "origin": "agent",
            "status": "needs-input",
            "messages": [{"id": "reply_1", "sender": "agent", "content": "draft"}],
        },
    )

    assert not any("DELETE FROM mira_control.messages" in query for query, _params in conn.cursor_obj.queries)


def test_upsert_legacy_item_does_not_stomp_newer_control_row():
    from control.repository import ControlRepository

    conn = FakeConn()
    conn.cursor_obj = FakeUpsertCursor(existing_updated_at="2026-05-02T14:21:12Z")
    repo = ControlRepository(conn)

    repo.upsert_bridge_item(
        "ang",
        {
            "id": "feed_market_1",
            "type": "feed",
            "title": "Market",
            "origin": "agent",
            "status": "done",
            "updated_at": "2026-05-02T11:08:28Z",
            "messages": [],
        },
    )

    assert not any("INSERT INTO" in query for query, _params in conn.cursor_obj.queries)


def test_upsert_status_card_keeps_user_origin_for_reopened_thread():
    from control.repository import ControlRepository

    conn = FakeConn()
    conn.cursor_obj = FakeUpsertCursor(existing_updated_at="2026-05-02T14:21:12Z")
    repo = ControlRepository(conn)

    repo.upsert_bridge_item(
        "ang",
        {
            "id": "feed_market_1",
            "type": "feed",
            "title": "Market",
            "origin": "agent",
            "status": "dispatched",
            "updated_at": "2026-05-02T14:23:52Z",
            "messages": [],
        },
    )

    params = next(params for query, params in conn.cursor_obj.queries if "INSERT INTO" in query)
    assert params["origin"] == "user"


def test_upsert_agent_feed_done_does_not_close_reopened_user_thread():
    from control.repository import ControlRepository

    conn = FakeConn()
    conn.cursor_obj = FakeUpsertCursor(existing_updated_at="2026-05-02T14:21:12Z")
    repo = ControlRepository(conn)

    repo.upsert_bridge_item(
        "ang",
        {
            "id": "health_today_ang",
            "type": "feed",
            "title": "今日健康",
            "origin": "agent",
            "status": "done",
            "updated_at": "2026-05-02T14:23:52Z",
            "messages": [{"id": "health_today_ang_digest", "sender": "health_agent", "content": "latest"}],
        },
    )

    params = next(params for query, params in conn.cursor_obj.queries if "INSERT INTO" in query)
    assert params["origin"] == "user"
    assert params["status"] == "queued"


def test_overlay_running_task_projects_status_card(tmp_path):
    import json

    from control.repository import ControlRepository

    workspace = tmp_path / "task"
    workspace.mkdir()
    (workspace / "heartbeat.json").write_text(
        json.dumps(
            {
                "status_text": "Step 1/2 · general: locating Tetra synthesis. Elapsed 5m; timeout guard in 10m.",
                "status_icon": "hourglass",
            }
        ),
        encoding="utf-8",
    )
    conn = FakeConn()
    repo = ControlRepository(conn)

    repo.overlay_task_record(
        {
            "task_id": "req_progress",
            "user_id": "ang",
            "content_preview": "tetra synthesis",
            "status": "running",
            "started_at": "2026-05-02T14:23:52Z",
            "workspace": str(workspace),
        }
    )

    message_query, message_params = next(
        (query, params) for query, params in conn.cursor_obj.queries if "INSERT INTO mira_control.messages" in query
    )
    assert "status_card" in message_query
    assert message_params[:3] == ("req_progress_status", "req_progress", "ang")
    assert "locating Tetra synthesis" in message_params[3]
