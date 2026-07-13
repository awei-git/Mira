from __future__ import annotations

from pathlib import Path


class _FakeActionItem:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeBacklog:
    last: "_FakeBacklog | None" = None

    def __init__(self):
        self.items = []
        _FakeBacklog.last = self

    def add(self, item):
        self.items.append(item)


def test_low_risk_eval_scaffold_change_requires_manual_review(monkeypatch, tmp_path: Path):
    import ops.backlog as backlog
    import self_evolve

    monkeypatch.setattr(backlog, "ActionBacklog", _FakeBacklog)
    monkeypatch.setattr(backlog, "ActionItem", _FakeActionItem)

    proposal = {
        "title": "Tighten evaluator",
        "description": "Add evaluator-only proxy drift checks.",
        "risk_level": "low",
        "files_affected": ["tests/evaluator/test_proxy_drift.py"],
    }

    self_evolve._enqueue_backlog_action(proposal, tmp_path / "proposal.json")

    assert _FakeBacklog.last is not None
    item = _FakeBacklog.last.items[0]
    assert item.status == "proposed"
    assert item.priority == "medium"
    assert item.executor == ""
    assert "requires manual review" in item.description


def test_eval_scaffold_change_with_independent_behavioral_verification_can_auto_queue(monkeypatch, tmp_path: Path):
    import ops.backlog as backlog
    import self_evolve

    monkeypatch.setattr(backlog, "ActionBacklog", _FakeBacklog)
    monkeypatch.setattr(backlog, "ActionItem", _FakeActionItem)

    proposal = {
        "title": "Tighten evaluator with runtime check",
        "description": "Add evaluator check plus an integration test in web/server.py that verifies user-visible output.",
        "risk_level": "low",
        "files_affected": ["tests/evaluator/test_proxy_drift.py", "web/server.py"],
    }

    self_evolve._enqueue_backlog_action(proposal, tmp_path / "proposal.json")

    assert _FakeBacklog.last is not None
    item = _FakeBacklog.last.items[0]
    assert item.status == "approved"
    assert item.priority == "high"
    assert item.executor == "self_evolve_proposal"
