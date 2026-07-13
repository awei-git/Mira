"""Shared helpers for the V3.1 executable eval checklist CLIs."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mira.engine.effect_log import EffectLog
from mira.evals import (
    _filter_by_timestamp,
    _is_synthetic_task_fixture_record,
    _weekly_window,
    filter_first_stage_eval_effects,
    filter_first_stage_eval_records,
)
from mira.kernel.commit import MemoryCommitLog
from mira.runtime import default_approval_store, default_causal_evidence_log, default_ledger, default_v3_paths


@dataclass(frozen=True)
class V31EvalInputs:
    root: Path
    week_label: str
    window_start: datetime
    window_end: datetime
    records: list
    commits: list
    effects: list
    causal_evidence: list
    approval_events: list
    synthetic_record_count: int
    first_stage_scope: bool
    first_stage_record_count: int


def add_common_eval_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, default=ROOT, help="Mira workspace root.")
    parser.add_argument("--week", help="Week label for the bounded eval window; defaults to today.")
    parser.add_argument("--window-days", type=int, default=7, help="Number of days in the eval window.")
    parser.add_argument(
        "--first-stage-scope",
        action="store_true",
        help="Limit run records/effects to the V3.1 first-stage workflows.",
    )
    parser.add_argument(
        "--include-synthetic",
        action="store_true",
        help="Include known synthetic task fixture records in the eval window.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")


def load_v31_eval_inputs(args: argparse.Namespace) -> V31EvalInputs:
    week_label = args.week or date.today().isoformat()
    window_start, window_end = _weekly_window(week_label, args.window_days)
    paths = default_v3_paths(args.root)

    records_all = _filter_by_timestamp(default_ledger(args.root).list(), window_start, window_end)
    records = list(records_all)
    if not args.include_synthetic:
        records = [record for record in records if not _is_synthetic_task_fixture_record(record)]
    synthetic_record_count = len(records_all) - len(records)
    records_after_synthetic_filter = list(records)
    if args.first_stage_scope:
        records = filter_first_stage_eval_records(records)
    first_stage_record_count = len(records_after_synthetic_filter) - len(records) if args.first_stage_scope else 0

    commits = _filter_by_timestamp(MemoryCommitLog(paths.commits).list(), window_start, window_end)
    effects = _filter_by_timestamp(EffectLog(paths.effect_log).list(), window_start, window_end)
    if args.first_stage_scope:
        effects = filter_first_stage_eval_effects(effects)

    return V31EvalInputs(
        root=args.root,
        week_label=week_label,
        window_start=window_start,
        window_end=window_end,
        records=records,
        commits=commits,
        effects=effects,
        causal_evidence=_filter_by_timestamp(default_causal_evidence_log(args.root).list(), window_start, window_end),
        approval_events=_filter_by_timestamp(default_approval_store(args.root).list_events(), window_start, window_end),
        synthetic_record_count=synthetic_record_count,
        first_stage_scope=bool(args.first_stage_scope),
        first_stage_record_count=first_stage_record_count,
    )


def base_payload(name: str, inputs: V31EvalInputs, passed: bool) -> dict[str, Any]:
    return {
        "eval": name,
        "passed": passed,
        "week_label": inputs.week_label,
        "window_start": inputs.window_start.date().isoformat(),
        "window_end": inputs.window_end.date().isoformat(),
        "record_count": len(inputs.records),
        "synthetic_record_count_excluded": inputs.synthetic_record_count,
        "first_stage_scope": inputs.first_stage_scope,
        "first_stage_record_count_excluded": inputs.first_stage_record_count,
    }


def json_ready(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return json_ready(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    return value


def emit_payload(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(json_ready(payload), indent=2, sort_keys=True))
        return
    verdict = "PASS" if payload.get("passed") else "FAIL"
    print(f"{verdict}: {payload.get('eval')}")
    for key, value in payload.items():
        if key in {"eval", "passed"}:
            continue
        print(f"- {key}: {json_ready(value)}")


def exit_code(passed: bool) -> int:
    return 0 if passed else 1
