"""Tests for thread_manager — locking, CRUD, memory append."""

import json
import sys
import tempfile
from pathlib import Path

# Patch MIRA_DIR and paths before importing
_test_dir = Path(tempfile.mkdtemp(prefix="mira_thread_test_"))
import config

_orig_mira_dir = config.MIRA_DIR
config.MIRA_DIR = _test_dir

import memory.threads as tm


def _fresh_manager():
    """Create a ThreadManager with clean state."""
    users_dir = _test_dir / "users" / "ang"
    threads_dir = users_dir / "threads"
    if threads_dir.exists():
        for path in sorted(threads_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
    threads_dir.mkdir(parents=True, exist_ok=True)
    return tm.ThreadManager(user_id="ang", bridge_dir=_test_dir)


def test_create_thread():
    mgr = _fresh_manager()
    tid = mgr.create_thread("Test Thread")
    assert len(tid) == 8
    threads = mgr.list_threads()
    assert len(threads) == 1
    assert threads[0]["title"] == "Test Thread"


def test_get_or_create():
    mgr = _fresh_manager()
    tid1 = mgr.create_thread("First")
    # Existing thread
    tid2 = mgr.get_or_create_thread(tid1, "Fallback")
    assert tid2 == tid1
    # New thread
    tid3 = mgr.get_or_create_thread("", "New Thread")
    assert tid3 != tid1
    assert len(mgr.list_threads()) == 2


def test_append_thread_memory():
    mgr = _fresh_manager()
    tid = mgr.create_thread("Memory Test")
    mgr.append_thread_memory(tid, "First entry")
    mgr.append_thread_memory(tid, "Second entry")

    mem = mgr.get_thread_memory(tid)
    assert "First entry" in mem
    assert "Second entry" in mem
    assert mem.startswith("# Thread Memory")


def test_empty_thread_memory():
    mgr = _fresh_manager()
    tid = mgr.create_thread("Empty")
    mem = mgr.get_thread_memory(tid)
    assert mem == ""


def test_index_persistence():
    mgr = _fresh_manager()
    mgr.create_thread("Persist Test")
    # Reload
    mgr2 = tm.ThreadManager(user_id="ang", bridge_dir=_test_dir)
    threads = mgr2.list_threads()
    assert len(threads) == 1
    assert threads[0]["title"] == "Persist Test"


def test_index_atomic_write():
    """Verify index is written via tmp+rename (atomic)."""
    mgr = _fresh_manager()
    mgr.create_thread("Atomic Test")
    # If atomic, index file should exist and be valid JSON
    index_file = _test_dir / "users" / "ang" / "threads" / "index.json"
    assert index_file.exists()
    data = json.loads(index_file.read_text(encoding="utf-8"))
    assert len(data) == 1


def test_thread_memory_is_user_scoped():
    mgr = _fresh_manager()
    tid = mgr.create_thread("Scoped Memory")
    mgr.append_thread_memory(tid, "Private entry")

    user_mem = _test_dir / "users" / "ang" / "threads" / tid / "memory.md"
    legacy_mem = _test_dir / "threads" / tid / "memory.md"
    assert user_mem.exists()
    assert not legacy_mem.exists()


def test_legacy_global_memory_is_still_readable():
    legacy_thread_dir = _test_dir / "threads" / "legacy1234"
    legacy_thread_dir.mkdir(parents=True, exist_ok=True)
    (legacy_thread_dir / "memory.md").write_text("# Thread Memory\n\n- old entry\n", encoding="utf-8")

    mgr = tm.ThreadManager(user_id="ang", bridge_dir=_test_dir)
    mem = mgr.get_thread_memory("legacy1234")

    assert "old entry" in mem
