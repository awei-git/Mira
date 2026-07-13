from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime as real_datetime
from pathlib import Path


def _load_marginalia():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "mira_marginalia_for_test",
        root / "agents" / "reader" / "mira_marginalia.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_episode_slug_is_ascii_and_stable():
    mod = _load_marginalia()

    slug = mod.episode_slug("2026-W25", {"title": "时间的秩序", "author": "Carlo Rovelli"})

    assert slug.startswith("mira-marginalia-2026-w25")
    assert re.fullmatch(r"[a-z0-9-]+", slug)


def test_quality_gate_enforces_15_minute_script_bounds():
    mod = _load_marginalia()

    good = "标题：测试\n\n" + "刺点" * 1450
    too_short = "标题：测试\n\n" + "刺点" * 200
    too_long = "标题：测试\n\n" + "刺点" * 2100
    generic = good + "引人深思"
    markdown = "标题：测试\n\n## 第一部分\n\n" + "刺点" * 1450

    assert mod.quality_issues(good) == []
    assert any("too short" in issue for issue in mod.quality_issues(too_short))
    assert any("too long" in issue for issue in mod.quality_issues(too_long))
    assert any("generic phrase" in issue for issue in mod.quality_issues(generic))
    assert any("markdown header" in issue for issue in mod.quality_issues(markdown))


def test_compose_and_finalize_episode_runs_review_revision(monkeypatch, tmp_path):
    mod = _load_marginalia()
    for day in range(1, 8):
        (tmp_path / f"day{day}.md").write_text(f"# Day {day}\n\n观点{day}" * 80, encoding="utf-8")

    def fake_model(prompt, model_name="", timeout=0):
        if "按审稿意见改成终稿" in prompt:
            return "标题：终题\n\n" + "刺点" * 1450
        if "中文播客脚本" in prompt:
            return "标题：初题\n\n" + "观点" * 1450
        return ""

    monkeypatch.setattr(mod, "model_think", fake_model)
    monkeypatch.setattr(mod, "claude_think", lambda prompt, timeout=0, tier="": "保留中心判断，砍掉松散段落。")

    title, critique, final = mod.compose_and_finalize_episode(
        {
            "book": {"title": "Test Book", "author": "A. Writer"},
            "series_dir": str(tmp_path),
        }
    )

    assert title == "终题"
    assert "保留中心判断" in critique
    assert mod.quality_issues(final) == []
    assert not final.startswith("标题")


def test_job_registry_contains_mira_marginalia_script_job():
    from runtime.jobs import get_job

    job = get_job("mira-marginalia")

    assert job is not None
    assert job.launcher == "script"
    assert job.script_path == "../reader/mira_marginalia.py"
    assert job.trigger_name == "should_mira_marginalia"


def test_marginalia_trigger_skips_completed_current_week(monkeypatch, tmp_path):
    from runtime import triggers

    class FakeDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 18, 10, 0, 0)

    monkeypatch.setattr(triggers, "datetime", FakeDateTime)
    monkeypatch.setattr(triggers, "SOUL_DIR", tmp_path)
    state_path = tmp_path / "mira_marginalia_state.json"
    state_path.write_text(
        json.dumps({"status": "complete", "week_id": "2026-W25", "last_run_date": "2026-06-17"}),
        encoding="utf-8",
    )
    assert triggers.should_mira_marginalia() is False

    state_path.write_text(
        json.dumps({"status": "complete", "week_id": "2026-W24", "last_run_date": "2026-06-17"}),
        encoding="utf-8",
    )
    assert triggers.should_mira_marginalia() is True
