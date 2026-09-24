"""Standalone API check on the actual supported Python runtimes; no model calls."""

from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "bridge"), str(ROOT / "lib")]
from api import create_app, TransitionIn
from obligations import Ledger
from fastapi.testclient import TestClient
from pydantic import ValidationError

for result in (None, "done", {"ok": True}):
    assert TransitionIn(expected_revision=0, status="succeeded", result=result).result == result
try:
    TransitionIn(expected_revision=0, status="succeeded", unauthorized=True)
except ValidationError:
    pass
else:
    raise AssertionError("unknown input fields must remain forbidden")
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    ledger = Ledger(root / "ledger.sqlite3", root / "artifacts")
    with TestClient(create_app(ledger, {"codex": "synthetic-test-token"})) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/events").status_code == 401
        headers = {"Authorization": "Bearer synthetic-test-token"}
        assert client.get("/events", headers=headers).status_code == 200
        response = client.post("/v1/tasks", headers=headers, json={"kind": "ping"})
        assert response.status_code == 200, response.text
        assert response.json()["result"] == "pong"
        assert client.get("/v1/tasks/" + response.json()["id"], headers=headers).json()["status"] == "done"
print("bridge runtime smoke passed on " + sys.version.split()[0])
