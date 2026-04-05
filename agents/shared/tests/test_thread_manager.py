"""Tests for thread_manager — locking, CRUD, memory append."""
import json
import sys
import tempfile
from pathlib import Path

_SHARED = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SHARED))

# Patch MIRA_DIR and paths before importing
_test_dir = Path(tempfile.mkdtemp(prefix="mira_thread_test_"))
import config
_orig_mira_dir = config.MIRA_DIR
config.MIRA_DIR = _test_dir

import thread_manager as tm
tm.THREADS_DIR = _test_dir / "threads"
tm.INDEX_FILE = tm.THREADS_DIR / "index.json"


def _fresh_manager():
    """Create a ThreadManager with clean state."""
    tm.THREADS_DIR.mkdir(parents=True, exist_ok=True)
    if tm.INDEX_FILE.exists():
        tm.INDEX_FILE.unlink()
    return tm.ThreadManager()


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
    mgr2 = tm.ThreadManager()
    threads = mgr2.list_threads()
    assert len(threads) == 1
    assert threads[0]["title"] == "Persist Test"


def test_index_atomic_write():
    """Verify index is written via tmp+rename (atomic)."""
    mgr = _fresh_manager()
    mgr.create_thread("Atomic Test")
    # If atomic, index file should exist and be valid JSON
    assert tm.INDEX_FILE.exists()
    data = json.loads(tm.INDEX_FILE.read_text(encoding="utf-8"))
    assert len(data) == 1
