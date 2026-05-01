from __future__ import annotations

import json


def test_reply_fingerprint_is_stable():
    import activity_inbox

    ctx = {"reply_cid": 123, "reply_name": "Ada", "reply_body": "Interesting point"}

    assert activity_inbox._reply_fingerprint("item-1", ctx) == activity_inbox._reply_fingerprint("item-1", dict(ctx))


def test_append_reply_shadow_writes_jsonl(tmp_path, monkeypatch):
    import config
    import activity_inbox

    monkeypatch.setattr(config, "SOCIAL_STATE_DIR", tmp_path)
    ctx = {"reply_cid": 123, "reply_name": "Ada", "reply_body": "Interesting point"}

    activity_inbox._append_reply_shadow("item-1", ctx, "candidate")

    path = tmp_path / "reply_dedup_shadow.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["item_key"] == "item-1"
    assert rows[0]["reply_cid"] == 123
    assert rows[0]["decision"] == "candidate"
    assert rows[0]["already_replied"] is False
    assert len(rows[0]["fingerprint"]) == 40
