from __future__ import annotations


def test_start_dispatch_workflow_returns_handle_id(monkeypatch):
    import runtime.task_workflow as task_workflow

    events = []

    class FakeHandle:
        def workflow_id(self):
            return "wf_123"

    class FakeDBOS:
        def step(self, **kwargs):
            def decorate(func):
                def wrapper(payload):
                    events.append(("step", kwargs, payload["task_id"]))
                    return func(payload)

                return wrapper

            return decorate

        def workflow(self, **kwargs):
            def decorate(func):
                def wrapper(payload):
                    events.append(("workflow", kwargs, payload["task_id"]))
                    return func(payload)

                return wrapper

            return decorate

        def start_workflow(self, workflow, payload):
            workflow(payload)
            return FakeHandle()

    class FakeRepo:
        def __init__(self, conn):
            self.conn = conn

        def record_task_event(self, user_id, task_id, event_type, *, status=None, payload=None):
            events.append((user_id, task_id, event_type, status, payload))

    class FakeTx:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(task_workflow, "_workflow_func", None)
    monkeypatch.setattr(task_workflow, "get_dbos", lambda: FakeDBOS())
    monkeypatch.setattr(task_workflow, "transaction", lambda: FakeTx())
    monkeypatch.setattr(task_workflow, "ControlRepository", FakeRepo)

    workflow_id = task_workflow.start_dispatch_workflow(
        {
            "task_id": "req_1",
            "user_id": "ang",
            "workflow_id": "req_1",
            "status": "dispatched",
            "pid": 123,
            "workspace": "/tmp/req_1",
            "attempt_count": 1,
        }
    )

    assert workflow_id == "wf_123"
    assert (
        "ang",
        "req_1",
        "workflow.dispatch_recorded",
        "dispatched",
        {
            "workflow_id": "req_1",
            "pid": 123,
            "workspace": "/tmp/req_1",
            "attempt_count": 1,
        },
    ) in events
