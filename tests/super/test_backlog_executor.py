"""Tests for backlog executor."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

_MIRA = Path(__file__).resolve().parent.parent.parent


def _make_backlog(tmp_path: Path):
    from ops.backlog import ActionBacklog

    path = tmp_path / "action_backlog.json"
    return ActionBacklog(path=path), path


def test_run_once_executes_approved_self_evolve_proposal(monkeypatch, tmp_path: Path):
    from ops.backlog import ActionItem
    import backlog_executor

    backlog, backlog_path = _make_backlog(tmp_path)
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(
        json.dumps({"title": "Improve retry handling", "risk_level": "low"}),
        encoding="utf-8",
    )
    backlog.add(
        ActionItem(
            title="Improve retry handling",
            description="test",
            source="self_evolve",
            status="approved",
            executor="self_evolve_proposal",
            payload={"proposal_path": str(proposal_path)},
        )
    )
    monkeypatch.setitem(
        sys.modules,
        "self_evolve",
        SimpleNamespace(auto_implement=lambda proposal, path: {"success": True, "reason": "tests passed"}),
    )

    result = backlog_executor.run_once(backlog_path=backlog_path)

    assert result["executed"] is True
    assert result["success"] is True
    verified = backlog.get_by_status("verified")
    assert len(verified) == 1
    assert verified[0].title == "Improve retry handling"


def test_run_once_rejects_missing_proposal(monkeypatch, tmp_path: Path):
    from ops.backlog import ActionItem, ActionBacklog
    import backlog_executor

    backlog, backlog_path = _make_backlog(tmp_path)
    backlog.add(
        ActionItem(
            title="Broken proposal",
            description="test",
            source="self_evolve",
            status="approved",
            executor="self_evolve_proposal",
            payload={"proposal_path": str(tmp_path / "missing.json")},
        )
    )

    result = backlog_executor.run_once(backlog_path=backlog_path)

    assert result["executed"] is True
    assert result["success"] is False
    reloaded = ActionBacklog(path=backlog_path)
    rejected = reloaded.get_by_status("rejected")
    assert len(rejected) == 1
    assert "proposal missing" in rejected[0].last_error
