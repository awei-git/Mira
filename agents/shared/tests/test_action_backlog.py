"""Tests for action backlog."""
import json
import sys
import tempfile
from pathlib import Path

_SHARED = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SHARED))


def _make_backlog():
    from action_backlog import ActionBacklog
    tmp = Path(tempfile.mktemp(suffix=".json"))
    return ActionBacklog(path=tmp), tmp


def test_add_and_retrieve():
    from action_backlog import ActionBacklog, ActionItem
    bl, tmp = _make_backlog()
    try:
        item = ActionItem(title="Fix bug", description="Important bug", source="reflect")
        assert bl.add(item)
        assert len(bl) == 1
        active = bl.get_active()
        assert len(active) == 1
        assert active[0].title == "Fix bug"
    finally:
        tmp.unlink(missing_ok=True)


def test_dedup():
    from action_backlog import ActionBacklog, ActionItem
    bl, tmp = _make_backlog()
    try:
        bl.add(ActionItem(title="Fix bug", description="v1", source="reflect"))
        assert not bl.add(ActionItem(title="Fix bug", description="v2", source="reflect"))
        assert len(bl) == 1
    finally:
        tmp.unlink(missing_ok=True)


def test_status_update():
    from action_backlog import ActionBacklog, ActionItem
    bl, tmp = _make_backlog()
    try:
        bl.add(ActionItem(title="Fix bug", description="test", source="reflect"))
        assert bl.update_status("Fix bug", "verified", "Fixed in PR #123")
        items = bl.get_by_status("verified")
        assert len(items) == 1
        assert items[0].resolution == "Fixed in PR #123"
    finally:
        tmp.unlink(missing_ok=True)


def test_persistence():
    from action_backlog import ActionBacklog, ActionItem
    bl, tmp = _make_backlog()
    try:
        bl.add(ActionItem(title="Persist test", description="test", source="manual"))
        bl2 = ActionBacklog(path=tmp)
        assert len(bl2) == 1
        assert bl2.get_active()[0].title == "Persist test"
    finally:
        tmp.unlink(missing_ok=True)


def test_summary():
    from action_backlog import ActionBacklog, ActionItem
    bl, tmp = _make_backlog()
    try:
        bl.add(ActionItem(title="A", description="a", source="reflect"))
        bl.add(ActionItem(title="B", description="b", source="reflect"))
        s = bl.summary()
        assert "2 active" in s
    finally:
        tmp.unlink(missing_ok=True)


def test_add_reloads_latest_state_before_write():
    from action_backlog import ActionBacklog, ActionItem
    bl1, tmp = _make_backlog()
    try:
        assert bl1.add(ActionItem(title="First", description="a", source="reflect"))
        bl2 = ActionBacklog(path=tmp)
        assert bl2.add(ActionItem(title="Second", description="b", source="reflect"))

        assert bl1.add(ActionItem(title="Third", description="c", source="reflect"))

        reloaded = ActionBacklog(path=tmp)
        assert sorted(item.title for item in reloaded.get_active()) == ["First", "Second", "Third"]
    finally:
        tmp.unlink(missing_ok=True)
        tmp.with_suffix(".lock").unlink(missing_ok=True)


def test_claim_next_approved_prefers_high_priority():
    from action_backlog import ActionItem

    bl, tmp = _make_backlog()
    try:
        bl.add(ActionItem(title="Low", description="a", source="reflect", status="approved", priority="low", executor="noop"))
        bl.add(ActionItem(title="High", description="b", source="reflect", status="approved", priority="high", executor="noop"))

        claimed = bl.claim_next_approved({"noop"})

        assert claimed is not None
        assert claimed.title == "High"
        reloaded = bl.get_by_status("in_progress")
        assert len(reloaded) == 1
        assert reloaded[0].title == "High"
    finally:
        tmp.unlink(missing_ok=True)
        tmp.with_suffix(".lock").unlink(missing_ok=True)


def test_finish_execution_records_verification():
    from action_backlog import ActionItem

    bl, tmp = _make_backlog()
    try:
        bl.add(ActionItem(title="Task", description="a", source="manual", status="approved", executor="noop"))
        claimed = bl.claim_next_approved({"noop"})
        assert claimed is not None

        assert bl.finish_execution(
            "Task",
            success=True,
            resolution="Applied safely",
            verification_summary="tests green",
        )

        verified = bl.get_by_status("verified")
        assert len(verified) == 1
        assert verified[0].verification_summary == "tests green"
        assert verified[0].verified_at
    finally:
        tmp.unlink(missing_ok=True)
        tmp.with_suffix(".lock").unlink(missing_ok=True)


def test_update_item_rejects_invalid_status():
    from action_backlog import ActionItem

    bl, tmp = _make_backlog()
    try:
        bl.add(ActionItem(title="Task", description="a", source="manual"))

        assert not bl.update_item("Task", status="bogus")

        active = bl.get_active()
        assert len(active) == 1
        assert active[0].status == "proposed"
    finally:
        tmp.unlink(missing_ok=True)
        tmp.with_suffix(".lock").unlink(missing_ok=True)
