from __future__ import annotations

import pytest

from locks.process import ProcessLockActive, launchagent_lock


def test_launchagent_lock_writes_and_clears_record(tmp_path):
    path = tmp_path / "launchagent.pid"

    with launchagent_lock(path=path, pid=123, now=lambda: 10.0):
        assert path.exists()
        assert '"pid": 123' in path.read_text(encoding="utf-8")

    assert not path.exists()


def test_launchagent_lock_blocks_fresh_live_pid(tmp_path):
    path = tmp_path / "launchagent.pid"
    with launchagent_lock(path=path, pid=123, now=lambda: 10.0):
        with pytest.raises(ProcessLockActive):
            with launchagent_lock(path=path, pid=456, now=lambda: 20.0, is_alive=lambda p: p == 123):
                pass


def test_launchagent_lock_takes_over_stale_pid(tmp_path):
    path = tmp_path / "launchagent.pid"

    with launchagent_lock(path=path, pid=123, now=lambda: 10.0):
        pass
    path.write_text('{"pid": 123, "started_at": 10.0, "heartbeat_at": 10.0}', encoding="utf-8")

    with launchagent_lock(path=path, pid=456, now=lambda: 400.0, ttl_s=300, is_alive=lambda p: True):
        assert '"pid": 456' in path.read_text(encoding="utf-8")
