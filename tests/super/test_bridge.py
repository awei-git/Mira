"""Bridge tests — verify Mira app communication layer."""

from __future__ import annotations
import json, tempfile, sys
from pathlib import Path
import pytest

try:
    from bridge import Mira

    _HAS_BRIDGE = True
except (ImportError, ModuleNotFoundError):
    _HAS_BRIDGE = False

_skip_no_bridge = pytest.mark.skipif(not _HAS_BRIDGE, reason="mira_bridge not available (CI)")


@_skip_no_bridge
def test_bridge_init():
    from bridge import Mira
    from config import MIRA_BRIDGE_DIR

    bridge = Mira(MIRA_BRIDGE_DIR)
    assert bridge.bridge_dir.exists(), f"Bridge dir doesn't exist: {bridge.bridge_dir}"


@_skip_no_bridge
def test_bridge_create_item():
    from bridge import Mira

    with tempfile.TemporaryDirectory() as tmpdir:
        bridge = Mira(Path(tmpdir))
        item = bridge.create_item(
            "test-item-001",
            "request",
            "Test Title",
            "Test message content",
            sender="agent",
            tags=["test"],
            origin="agent",
        )
        assert item["id"] == "test-item-001"
        assert item["title"] == "Test Title"
        assert len(item["messages"]) == 1
        assert item["messages"][0]["content"] == "Test message content"


@_skip_no_bridge
def test_bridge_message_format():
    from bridge import Mira

    with tempfile.TemporaryDirectory() as tmpdir:
        bridge = Mira(Path(tmpdir))
        item = bridge.create_item(
            "test-fmt",
            "alert",
            "Alert Test",
            "Something happened",
            sender="agent",
        )
        msg = item["messages"][0]
        assert "id" in msg, "Message missing id"
        assert "sender" in msg, "Message missing sender"
        assert "content" in msg, "Message missing content"
        assert "timestamp" in msg, "Message missing timestamp"
        assert "kind" in msg, "Message missing kind"
