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
        assert bl.update_status("Fix bug", "implemented", "Fixed in PR #123")
        items = bl.get_by_status("implemented")
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
