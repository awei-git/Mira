"""Tests for declarative job registry."""
import sys
from pathlib import Path
from types import SimpleNamespace

_SUPER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SUPER))
sys.path.insert(0, str(_SUPER.parent / "lib"))


def test_all_jobs_have_names():
    from runtime.jobs import BACKGROUND_JOBS
    names = [j.name for j in BACKGROUND_JOBS]
    assert len(names) == len(set(names)), f"Duplicate job names: {names}"
    assert all(n for n in names), "All jobs must have non-empty names"


def test_get_jobs():
    from runtime.jobs import get_jobs
    jobs = get_jobs()
    assert len(jobs) > 15, f"Expected 15+ jobs, got {len(jobs)}"


def test_get_job_by_name():
    from runtime.jobs import get_job
    j = get_job("journal")
    assert j is not None
    assert j.name == "journal"
    assert j.trigger == "time_window"


def test_job_in_window():
    from runtime.jobs import get_job
    j = get_job("self-audit")
    assert j is not None
    assert j.in_window(9)  # 8-10 window
    assert not j.in_window(15)


def test_job_state_key():
    from runtime.jobs import get_job
    j = get_job("journal")
    assert j is not None
    key = j.state_key(today="2026-04-04")
    assert "2026-04-04" in key


def test_list_job_names():
    from runtime.jobs import list_job_names
    names = list_job_names()
    assert "journal" in names
    assert "explore" in names
    assert "self-evolve" in names
    assert names == sorted(names), "Should be sorted"


def test_inline_jobs():
    from runtime.jobs import BACKGROUND_JOBS
    inline = [j for j in BACKGROUND_JOBS if j.inline]
    assert len(inline) >= 2, "At least health-check and log-cleanup should be inline"
    inline_names = {j.name for j in inline}
    assert "health-check" in inline_names
    assert "log-cleanup" in inline_names


def test_evaluate_job_payload_filters_shared_trigger(monkeypatch):
    from runtime.jobs import evaluate_job_payload, get_job

    job = get_job("analyst-pre")
    assert job is not None

    monkeypatch.setattr("runtime.triggers.should_analyst", lambda: "0700")
    assert evaluate_job_payload(job) == "0700"

    monkeypatch.setattr("runtime.triggers.should_analyst", lambda: "1800")
    assert evaluate_job_payload(job) is None


def test_build_job_dispatch_formats_dynamic_templates():
    from runtime.jobs import build_job_dispatch, get_job

    job = get_job("explore")
    assert job is not None

    bg_name, cmd = build_job_dispatch(
        job,
        {"label": "arxiv_hf", "sources": ["arxiv", "huggingface"]},
        python_executable="python3",
        core_path="/tmp/core.py",
    )

    assert bg_name == "explore-arxiv_hf"
    assert cmd == [
        "python3",
        "/tmp/core.py",
        "explore",
        "--sources",
        "arxiv,huggingface",
        "--slot",
        "arxiv_hf",
    ]


def test_build_job_dispatch_appends_user_flag_for_per_user_jobs():
    from runtime.jobs import build_job_dispatch, get_job

    job = get_job("idle-think")
    assert job is not None
    assert job.per_user is True

    bg_name, cmd = build_job_dispatch(
        job,
        True,
        python_executable="python3",
        core_path="/tmp/core.py",
        user_id="liquan",
    )

    assert bg_name == "idle-think-liquan"
    assert cmd == [
        "python3",
        "/tmp/core.py",
        "idle-think",
        "--user",
        "liquan",
    ]


def test_spark_check_job_is_per_user():
    from runtime.jobs import get_job

    job = get_job("spark-check")

    assert job is not None
    assert job.per_user is True
    assert job.bg_name_pattern == "spark-check-{user_id}"


def test_soul_question_job_is_per_user():
    from runtime.jobs import get_job

    job = get_job("soul-question")

    assert job is not None
    assert job.per_user is True
    assert job.bg_name_pattern == "soul-question-{user_id}"


def test_backlog_and_restore_triggers_do_not_write_state(monkeypatch):
    from datetime import datetime as real_datetime
    from runtime import triggers

    class FakeDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 4, 6, 14, 0, 0)

    monkeypatch.setattr(triggers, "datetime", FakeDateTime)
    monkeypatch.setattr(triggers, "_load_state", lambda user_id=None: {})
    saves = []
    monkeypatch.setattr(triggers, "_save_state", lambda state, user_id=None: saves.append((state, user_id)))

    class FakeBacklog:
        def get_active(self):
            return [SimpleNamespace(status="approved", executor="self_evolve_proposal")]

    monkeypatch.setitem(sys.modules, "ops.backlog", SimpleNamespace(ActionBacklog=FakeBacklog))
    assert triggers._should_backlog_executor() is True

    monkeypatch.setitem(sys.modules, "restore_drill", SimpleNamespace(latest_backup_dir=lambda: "/tmp/backup"))
    assert triggers._should_restore_dry_run() is True

    assert saves == []
