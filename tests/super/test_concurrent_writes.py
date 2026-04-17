"""Test concurrent writes to soul files don't lose data.

Verifies that _locked_write and _locked_read_modify_write prevent data loss
when multiple processes write simultaneously.
"""

from __future__ import annotations
import multiprocessing
import sys
import tempfile
from pathlib import Path


def _ensure_path():
    """Multiprocessing workers don't inherit conftest sys.path."""
    import sys
    from pathlib import Path

    lib = str(Path(__file__).resolve().parent.parent.parent / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)


def _append_worker(args):
    """Worker that appends a unique line to a file under lock."""
    _ensure_path()
    file_path, worker_id, n_writes = args
    from memory.soul import _locked_read_modify_write

    for i in range(n_writes):

        def _modify(text):
            return text + f"worker-{worker_id}-{i}\n"

        _locked_read_modify_write(file_path, _modify)


def _overwrite_worker(args):
    """Worker that overwrites a file under lock."""
    _ensure_path()
    file_path, worker_id, n_writes = args
    from memory.soul import _locked_write

    for i in range(n_writes):
        _locked_write(file_path, f"worker-{worker_id}-write-{i}\n")


def test_concurrent_append_no_data_loss():
    """Multiple processes appending to same file should not lose any lines."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "test_memory.md"
        file_path.write_text("", encoding="utf-8")

        n_workers = 8
        n_writes = 50
        tasks = [(file_path, w, n_writes) for w in range(n_workers)]

        ctx = multiprocessing.get_context("fork")
        with ctx.Pool(n_workers) as pool:
            pool.map(_append_worker, tasks)

        content = file_path.read_text(encoding="utf-8")
        lines = [l for l in content.splitlines() if l.strip()]

        expected = n_workers * n_writes
        assert len(lines) == expected, (
            f"Expected {expected} lines, got {len(lines)}. " f"Lost {expected - len(lines)} writes!"
        )

        # Verify every worker's writes are present
        for w in range(n_workers):
            for i in range(n_writes):
                assert f"worker-{w}-{i}" in content, f"Missing: worker-{w}-{i}"


def test_concurrent_overwrite_no_corruption():
    """Multiple processes overwriting same file should produce valid content."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "test_worldview.md"
        file_path.write_text("initial\n", encoding="utf-8")

        n_workers = 8
        n_writes = 50
        tasks = [(file_path, w, n_writes) for w in range(n_workers)]

        with multiprocessing.get_context("fork").Pool(n_workers) as pool:
            pool.map(_overwrite_worker, tasks)

        # File should contain exactly one complete line from some worker
        content = file_path.read_text(encoding="utf-8")
        assert content.startswith("worker-"), f"Corrupted content: {content[:100]}"
        assert content.count("\n") == 1, f"Multiple lines in overwrite file"


def test_atomic_write_no_partial():
    """Atomic write should never leave partial content."""
    from memory.soul import _atomic_write

    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "test_atomic.md"

        # Write a large string
        big_content = "x" * 100_000 + "\nEND\n"
        _atomic_write(file_path, big_content)

        result = file_path.read_text(encoding="utf-8")
        assert result == big_content, "Content mismatch after atomic write"
        assert result.endswith("END\n"), "Partial write detected"


def test_locked_read_modify_write_creates_file():
    """_locked_read_modify_write should create file if it doesn't exist."""
    from memory.soul import _locked_read_modify_write

    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "new_file.md"
        assert not file_path.exists()

        _locked_read_modify_write(file_path, lambda t: "created\n")

        assert file_path.exists()
        assert file_path.read_text(encoding="utf-8") == "created\n"
