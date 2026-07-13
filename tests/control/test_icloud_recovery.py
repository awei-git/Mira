from __future__ import annotations

import json


def test_icloud_recovery_dry_run_maps_legacy_request(tmp_path):
    from dr.icloud_recovery import import_user_commands

    commands = tmp_path / "users" / "default" / "commands"
    commands.mkdir(parents=True)
    (commands / "cmd_20260501_abc.json").write_text(
        json.dumps(
            {
                "id": "abc",
                "type": "new_request",
                "sender": "default",
                "title": "Recovered",
                "content": "recover this",
            }
        ),
        encoding="utf-8",
    )

    results = import_user_commands("default", root=tmp_path, dry_run=True)

    assert results == [
        {
            "path": str(commands / "cmd_20260501_abc.json"),
            "imported": False,
            "dry_run": True,
            "task_id": "req_abc",
            "type": "new_request",
        }
    ]
