from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_comparative_project():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "comparative_book_project_for_test",
        root / "agents" / "reader" / "comparative_book_project.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_comparative_project_declares_thirty_chinese_points():
    mod = _load_comparative_project()

    assert mod.TOTAL_POINTS == 30
    assert len(mod.POINTS) == 30
    assert [book["title"] for book in mod.PROJECT_BOOKS] == [
        "百年孤独",
        "酒吧长谈",
        "佩德罗巴拉莫",
    ]
    assert all(not any("A" <= char <= "z" for char in point) for point in mod.POINTS)


def test_job_registry_contains_comparative_script_job():
    from runtime.jobs import get_job

    job = get_job("comparative-book-project")

    assert job is not None
    assert job.launcher == "script"
    assert job.script_path == "../reader/comparative_book_project.py"
    assert job.trigger_name == "should_comparative_book_project"
    assert job.state_key_pattern == "comparative_book_project_{date}"
