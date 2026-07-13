"""Action-risk catalog loader."""

from __future__ import annotations

from pathlib import Path

import yaml


def load_action_risk_catalog(path: Path | str | None = None) -> dict[str, dict[str, bool]]:
    target = Path(path) if path else Path(__file__).with_name("action_risk.yaml")
    return yaml.safe_load(target.read_text(encoding="utf-8")) or {}


def risk_requires_grant(risk: str, path: Path | str | None = None) -> bool:
    catalog = load_action_risk_catalog(path)
    return bool(catalog.get(risk, {}).get("grant_required", False))
