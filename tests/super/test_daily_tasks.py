from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "agents" / "super"))

import daily_tasks  # noqa: E402


def test_merged_daily_state_includes_user_namespace(monkeypatch):
    def fake_load_state(user_id=None):
        if user_id == "ang":
            return {"zhesi_2026-05-01": "done", "journal_2026-05-01_actor": "journal/claude-think"}
        return {"analyst_2026-05-01_0700": True}

    monkeypatch.setattr(daily_tasks, "load_state", fake_load_state)

    state = daily_tasks._merged_daily_state()

    assert state["analyst_2026-05-01_0700"] is True
    assert state["zhesi_2026-05-01"] == "done"
    assert state["journal_2026-05-01_actor"] == "journal/claude-think"


def test_daily_output_verifiers_accept_real_outputs(monkeypatch, tmp_path):
    journal_dir = tmp_path / "journal"
    bridge_dir = tmp_path / "bridge"
    journal_dir.mkdir()
    (journal_dir / "2026-05-01.md").write_text("# Journal", encoding="utf-8")
    (journal_dir / "2026-05-01_zhesi.md").write_text("# Zhesi", encoding="utf-8")
    items_dir = bridge_dir / "users" / "ang" / "items"
    items_dir.mkdir(parents=True)
    (items_dir / "soul_question_20260501.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(daily_tasks, "JOURNAL_DIR", journal_dir)
    monkeypatch.setattr(daily_tasks, "MIRA_DIR", bridge_dir)

    assert daily_tasks._verify_journal({}, "2026-05-01")
    assert daily_tasks._verify_zhesi({}, "2026-05-01")
    assert daily_tasks._verify_soul_question({}, "2026-05-01")


def test_self_evolve_verifier_uses_data_proposals(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    proposals_dir = data_dir / "proposals"
    proposals_dir.mkdir(parents=True)
    (proposals_dir / "2026-05-01_test.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(daily_tasks, "DATA_DIR", data_dir)

    assert daily_tasks._verify_self_evolve({"self_evolve_2026-05-01": "done"}, "2026-05-01")
