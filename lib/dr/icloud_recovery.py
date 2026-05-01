from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import MIRA_DIR
from control.db import transaction
from control.repository import ControlRepository


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_command(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _task_id_for(cmd: dict[str, Any]) -> str:
    item_id = str(cmd.get("item_id") or "").strip()
    if item_id:
        return item_id
    cmd_id = str(cmd.get("id") or "").strip()
    if cmd.get("type") == "new_discussion":
        return cmd_id if cmd_id.startswith("disc_") else f"disc_{cmd_id}"
    return cmd_id if cmd_id.startswith("req_") else f"req_{cmd_id}"


def import_command_file(user_id: str, path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    cmd = _load_command(path)
    if not cmd:
        return {"path": str(path), "imported": False, "reason": "invalid_json"}
    cmd_type = str(cmd.get("type") or "")
    if cmd_type not in {"new_request", "new_discussion", "reply"}:
        return {"path": str(path), "imported": False, "reason": f"unsupported_type:{cmd_type}"}

    task_id = _task_id_for(cmd)
    if not task_id:
        return {"path": str(path), "imported": False, "reason": "missing_task_id"}

    if dry_run:
        return {"path": str(path), "imported": False, "dry_run": True, "task_id": task_id, "type": cmd_type}

    with transaction() as conn:
        repo = ControlRepository(conn)
        if cmd_type == "reply":
            repo.append_user_reply(
                user_id=user_id,
                task_id=task_id,
                message_id=str(cmd.get("id") or path.stem),
                sender=str(cmd.get("sender") or user_id),
                content=str(cmd.get("content") or ""),
                created_at=str(cmd.get("timestamp") or _utc_iso()),
            )
        else:
            repo.create_task(
                user_id=user_id,
                task_id=task_id,
                message_id=str(cmd.get("id") or path.stem),
                title=str(cmd.get("title") or cmd.get("content") or task_id)[:500],
                content=str(cmd.get("content") or ""),
                sender=str(cmd.get("sender") or user_id),
                item_type="discussion" if cmd_type == "new_discussion" else "request",
                quick=bool(cmd.get("quick", False)),
                tags=cmd.get("tags") if isinstance(cmd.get("tags"), list) else [],
                created_at=str(cmd.get("timestamp") or _utc_iso()),
            )
    archive_dir = path.parent / "imported"
    archive_dir.mkdir(exist_ok=True)
    shutil.move(str(path), str(archive_dir / path.name))
    return {"path": str(path), "imported": True, "task_id": task_id, "type": cmd_type}


def import_user_commands(user_id: str, *, root: Path = MIRA_DIR, dry_run: bool = False) -> list[dict[str, Any]]:
    commands_dir = root / "users" / user_id / "commands"
    if not commands_dir.exists():
        return []
    return [import_command_file(user_id, path, dry_run=dry_run) for path in sorted(commands_dir.glob("cmd_*.json"))]


def main() -> int:
    parser = argparse.ArgumentParser(description="Manually import legacy iCloud command files into Postgres.")
    parser.add_argument("user_id")
    parser.add_argument("--root", type=Path, default=MIRA_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    for result in import_user_commands(args.user_id, root=args.root, dry_run=args.dry_run):
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
