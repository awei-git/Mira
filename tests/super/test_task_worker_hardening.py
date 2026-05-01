from __future__ import annotations


def test_load_matching_progress_ignores_other_task(tmp_path):
    import task_worker

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "progress.md").write_text(
        "# Progress — req_old\n\n## Status: blocked\n",
        encoding="utf-8",
    )

    assert task_worker._load_matching_progress(workspace, "req_new") == ""


def test_load_matching_progress_accepts_current_task(tmp_path):
    import task_worker

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    progress = "# Progress — req_current\n\n## Status: running\n"
    (workspace / "progress.md").write_text(progress, encoding="utf-8")

    assert task_worker._load_matching_progress(workspace, "req_current") == progress
