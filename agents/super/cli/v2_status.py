"""Print V2 execution status from local status files.

This is intentionally dependency-light. Week 0 needs a stable command that works
before the Postgres/dashboard pieces exist; later weeks can replace the data
loaders behind this interface.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - PyYAML is a project dependency.
    yaml = None


ROOT = Path(__file__).resolve().parents[3]
STATUS_DIR = ROOT / "data" / "v2_status"
CURRENT_PLAN = ROOT / "docs" / "CURRENT_PLAN.md"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists() or yaml is None:
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _count_gate_status(gates: dict[str, Any], week: str) -> tuple[int, int, int]:
    criteria = gates.get(week, {}).get("criteria", [])
    if not isinstance(criteria, list):
        return (0, 0, 0)
    done = sum(1 for item in criteria if item.get("status") == "done")
    blocked = sum(1 for item in criteria if item.get("status") == "blocked")
    total = len(criteria)
    return (done, blocked, total)


def _current_week(start: date, today: date) -> int:
    delta_days = max((today - start).days, 0)
    if delta_days < 7:
        return 0
    return min((delta_days // 7), 6)


def render_status(today: date | None = None) -> str:
    today = today or date.today()
    gates = _load_yaml(STATUS_DIR / "gates.yaml")
    meta = gates.get("meta", {})
    start = date.fromisoformat(meta.get("start_date", "2026-05-01"))
    end = date.fromisoformat(meta.get("end_date", "2026-06-12"))
    week_no = _current_week(start, today)
    week_key = f"week_{week_no}"
    done, blocked, total = _count_gate_status(gates, week_key)

    plan_line = "missing"
    if CURRENT_PLAN.exists():
        plan_line = CURRENT_PLAN.read_text(encoding="utf-8").strip()

    blockers = gates.get(week_key, {}).get("blockers", [])
    if not isinstance(blockers, list):
        blockers = []
    blocker_text = ", ".join(blockers) if blockers else "none recorded"

    recommended = gates.get(week_key, {}).get("recommended_next", "not set")
    title = f"V2 Status — Week {week_no} of 6 — {today.isoformat()}"
    lines = [
        "=" * len(title),
        title,
        "=" * len(title),
        "",
        f"Plan: {plan_line}",
        f"Window: {start.isoformat()} to {end.isoformat()}",
        "",
        f"This week's gate: {week_key}",
        f"  done: {done}/{total}",
        f"  blocked: {blocked}/{total}",
        f"  blockers: {blocker_text}",
        "",
        f"Today's recommended V2 work: {recommended}",
        f"Status dir: {STATUS_DIR}",
        "",
        "Use `.venv/bin/python -m agents.super.cli.v2_status --gates` to print gate criteria.",
    ]
    return "\n".join(lines)


def render_gates() -> str:
    gates = _load_yaml(STATUS_DIR / "gates.yaml")
    lines: list[str] = []
    for week_key in [f"week_{idx}" for idx in range(7)]:
        week = gates.get(week_key, {})
        if not week:
            continue
        lines.append(f"{week_key}: {week.get('description', '')}")
        for item in week.get("criteria", []):
            status = item.get("status", "todo")
            owner = item.get("owner", "unset")
            lines.append(f"  [{status}] {item.get('id')} ({owner})")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Show Mira V2 execution status.")
    parser.add_argument("--gates", action="store_true", help="Print all gate criteria.")
    args = parser.parse_args()
    print(render_gates() if args.gates else render_status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
