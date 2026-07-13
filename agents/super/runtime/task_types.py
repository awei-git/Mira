from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


REGISTRY_PATH = Path(__file__).resolve().parent / "registry" / "task_types.yaml"


@dataclass(frozen=True)
class TaskTypeSpec:
    name: str
    verifier: str
    expected_observable_outcome: str
    min_size_bytes: int = 1
    match: dict[str, list[str]] | None = None


def _list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


@lru_cache(maxsize=1)
def load_task_type_specs() -> dict[str, TaskTypeSpec]:
    raw = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    defaults = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
    specs: dict[str, TaskTypeSpec] = {}
    for name, entry in (raw.get("task_types") or {}).items():
        if not isinstance(entry, dict):
            continue
        match = entry.get("match") if isinstance(entry.get("match"), dict) else {}
        specs[str(name)] = TaskTypeSpec(
            name=str(name),
            verifier=str(entry.get("verifier") or defaults.get("verifier") or ""),
            expected_observable_outcome=str(
                entry.get("expected_observable_outcome") or defaults.get("expected_observable_outcome") or ""
            ),
            min_size_bytes=int(entry.get("min_size_bytes") or defaults.get("min_size_bytes") or 1),
            match={
                "tags": _list(match.get("tags")),
                "agents": _list(match.get("agents")),
                "statuses": _list(match.get("statuses")),
            },
        )
    default_name = str(defaults.get("task_type") or "generic_request")
    if default_name not in specs:
        specs[default_name] = TaskTypeSpec(
            name=default_name,
            verifier=str(defaults.get("verifier") or "runtime.verifiers.output_file_min_size"),
            expected_observable_outcome=str(defaults.get("expected_observable_outcome") or ""),
            min_size_bytes=int(defaults.get("min_size_bytes") or 1),
            match={"tags": [], "agents": [], "statuses": []},
        )
    return specs


def resolve_task_type(*, tags: list[str] | None = None, agent: str = "", status: str = "") -> TaskTypeSpec:
    specs = load_task_type_specs()
    tag_set = {tag.lower() for tag in tags or []}
    agent_l = agent.lower()
    status_l = status.lower()
    for spec in specs.values():
        match = spec.match or {}
        if status_l and status_l in {s.lower() for s in match.get("statuses", [])}:
            return spec
        if agent_l and agent_l in {a.lower() for a in match.get("agents", [])}:
            return spec
        if tag_set & {t.lower() for t in match.get("tags", [])}:
            return spec
    return specs.get("generic_request") or next(iter(specs.values()))
