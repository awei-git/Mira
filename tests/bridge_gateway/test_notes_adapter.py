"""NotesBridgeAdapter — composition over lib/bridge.py::Mira."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from bridge_gateway import BridgeMessage
from bridge_gateway.adapters.notes import NotesBridgeAdapter


def _seed_bridge_layout(tmp_path):
    users = tmp_path / "users"
    (users / "ang" / "items").mkdir(parents=True)
    hb = tmp_path / "heartbeat.json"
    hb.write_text(
        json.dumps({"timestamp": "2026-04-16T23:00:00+00:00", "status": "online"}),
        encoding="utf-8",
    )
    return tmp_path


def test_read_incoming_is_passive(tmp_path):
    _seed_bridge_layout(tmp_path)
    adapter = NotesBridgeAdapter(bridge_dir=tmp_path, user_id="ang")
    assert adapter.read_incoming() == []


def test_send_outgoing_creates_new_item(tmp_path):
    _seed_bridge_layout(tmp_path)
    adapter = NotesBridgeAdapter(bridge_dir=tmp_path, user_id="ang")

    msg = BridgeMessage(
        id="outbound-1",
        user_id="ang",
        source="notes",
        content="hello from adapter\nwith a second line",
        tags=["system"],
    )
    ok = adapter.send_outgoing(msg)
    assert ok is True

    items = list((tmp_path / "users" / "ang" / "items").glob("outbound-1*"))
    assert items, "expected a new item file"


def test_send_outgoing_appends_to_existing_item(tmp_path):
    _seed_bridge_layout(tmp_path)
    adapter = NotesBridgeAdapter(bridge_dir=tmp_path, user_id="ang")
    # Create the item first
    first = BridgeMessage(id="thread-1", user_id="ang", source="notes", content="first reply")
    assert adapter.send_outgoing(first) is True
    # Append
    follow = BridgeMessage(
        id="reply-msg",
        user_id="ang",
        source="notes",
        content="follow-up",
        reply_to="thread-1",
    )
    assert adapter.send_outgoing(follow) is True


def test_heartbeat_parses_iso(tmp_path):
    _seed_bridge_layout(tmp_path)
    adapter = NotesBridgeAdapter(bridge_dir=tmp_path, user_id="ang")
    hb = adapter.heartbeat()
    assert hb.tzinfo is not None
    assert hb.year == 2026


def test_heartbeat_missing_file_returns_epoch(tmp_path):
    users = tmp_path / "users" / "ang" / "items"
    users.mkdir(parents=True)
    adapter = NotesBridgeAdapter(bridge_dir=tmp_path, user_id="ang")
    hb = adapter.heartbeat()
    assert hb == datetime.fromtimestamp(0, tz=timezone.utc)
