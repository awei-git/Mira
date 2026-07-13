from __future__ import annotations

import errno
import json
from pathlib import Path

from agents.health import ingest


class FakeStore:
    def __init__(self):
        self.inserted = []

    def insert_metrics_batch(self, person_id: str, metrics: list[dict], source: str = "apple_health"):
        self.inserted.append((person_id, metrics, source))


def test_ingest_apple_health_retries_transient_icloud_deadlock(monkeypatch, tmp_path: Path):
    export_dir = tmp_path / "users" / "default" / "health"
    export_dir.mkdir(parents=True)
    export_file = export_dir / "apple_health_export.json"
    export_file.write_text(
        json.dumps({"metrics": [{"type": "steps", "value": 1000, "unit": "count", "date": "2026-05-26T04:00:00Z"}]}),
        encoding="utf-8",
    )
    calls = {"count": 0}
    real_read_text = Path.read_text

    def flaky_read_text(path, *args, **kwargs):
        if path == export_file and calls["count"] < 2:
            calls["count"] += 1
            raise OSError(errno.EDEADLK, "Resource deadlock avoided")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)
    monkeypatch.setattr(ingest, "HEALTH_EXPORT_RETRY_DELAY_SECONDS", 0)
    store = FakeStore()

    assert ingest.ingest_apple_health(tmp_path, "default", store) == 1
    assert store.inserted[0][0] == "default"
    assert store.inserted[0][2] == "apple_health"
    assert not export_file.exists()
