"""Inspect open V3 effect-log rows that require reconciliation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mira.engine.effect_log import EffectLogEntry, ReconciliationResult
from mira.engine.effect_resolvers import (
    resolve_effect_from_provider_state_manifests,
    resolve_rss_publish_from_feeds,
    resolve_substack_publish_from_manifest,
)
from mira.runtime import default_effect_log


def inspect_effect(
    *,
    root: Path | str = ROOT,
    effect_id: str = "",
    idempotency_key: str = "",
    publish_manifest_path: Path | str | None = None,
    rss_feed_paths: list[Path | str] | None = None,
    provider_state_dir: Path | str | None = None,
    provider_state_manifest_paths: list[Path | str] | None = None,
) -> dict[str, Any]:
    root_path = Path(root)
    effect = _find_effect(root, effect_id=effect_id, idempotency_key=idempotency_key)
    replay_bundle = _replay_bundle_summary(effect.replay_bundle_ref)
    payload = {
        "effect": effect.to_dict(),
        "replay_bundle": replay_bundle,
        "provider_evidence": _provider_evidence_summary(
            effect,
            publish_manifest_path=publish_manifest_path,
            rss_feed_paths=rss_feed_paths or [],
            provider_state_dir=(
                Path(provider_state_dir)
                if provider_state_dir is not None
                else root_path / "data" / "v3" / "provider_state"
            ),
            provider_state_manifest_paths=provider_state_manifest_paths or [],
            replay_bundle=replay_bundle,
        ),
        "next_steps": [
            "Inspect the replay bundle and provider state before retrying the side effect.",
            "Only reconcile when provider evidence proves success or failure.",
            "Do not mark this effect complete from local intent alone.",
        ],
    }
    return payload


def render_effect_inspection(payload: dict[str, Any]) -> str:
    effect = payload["effect"]
    replay = payload["replay_bundle"]
    provider_evidence = payload.get("provider_evidence", {})
    provider_state = provider_evidence.get("provider_state", {})
    provider_result = provider_state.get("result", {})
    publish_manifest = provider_evidence.get("publish_manifest", {})
    rss_feeds = provider_evidence.get("rss_feeds", {})
    lines = [
        "Mira V3 Effect Reconciliation",
        "=============================",
        "",
        f"Effect id: {effect.get('effect_id', '')}",
        f"Idempotency key: {effect.get('idempotency_key', '')}",
        f"Status: {effect.get('status', '')}",
        f"Pipeline/action: {effect.get('pipeline', '')}.{effect.get('action', '')}",
        f"Target: {effect.get('target', '')}",
        f"Replay bundle: {replay.get('path') or '(none)'}",
        f"Replay bundle status: {replay.get('status', '')}",
        f"Replay bundle provider evidence: {provider_evidence.get('replay_bundle_provider_evidence', '')}",
        f"Publish manifest result: {publish_manifest.get('result', {}).get('status', 'not_checked')}",
        f"RSS feed result: {rss_feeds.get('result', {}).get('status', 'not_checked')}",
        f"Provider state manifests checked: {provider_state.get('checked', 0)}",
        f"Provider state result: {provider_result.get('status', 'no_match')}",
        f"External ref: {effect.get('external_ref') or '(none)'}",
        f"Reconciliation ref: {effect.get('reconciliation_ref') or '(none)'}",
        "",
        "Next steps:",
        *[f"- {step}" for step in payload["next_steps"]],
    ]
    return "\n".join(lines)


def _find_effect(root: Path | str, *, effect_id: str, idempotency_key: str) -> EffectLogEntry:
    if not effect_id and not idempotency_key:
        raise ValueError("provide --effect-id or --idempotency-key")
    effects = default_effect_log(root).list()
    for effect in reversed(effects):
        if effect_id and effect.effect_id == effect_id:
            return effect
        if idempotency_key and effect.idempotency_key == idempotency_key:
            return effect
    identifier = effect_id or idempotency_key
    raise KeyError(f"No effect log entry found for {identifier}")


def _replay_bundle_summary(ref: str) -> dict[str, Any]:
    if not ref:
        return {"path": "", "status": "missing"}
    path = Path(ref)
    if not path.exists():
        return {"path": ref, "status": "not_found"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"path": ref, "status": "unreadable", "error": str(exc)}
    return {
        "path": ref,
        "status": "readable_json",
        "keys": sorted(str(key) for key in payload.keys()),
        "compensation_strategy": payload.get("compensation_strategy") or payload.get("compensation", {}),
        "provider_evidence": payload.get("provider_evidence"),
    }


def _provider_evidence_summary(
    effect: EffectLogEntry,
    *,
    publish_manifest_path: Path | str | None,
    rss_feed_paths: list[Path | str],
    provider_state_dir: Path,
    provider_state_manifest_paths: list[Path | str],
    replay_bundle: dict[str, Any],
) -> dict[str, Any]:
    manifest_paths = _provider_state_paths(provider_state_dir, provider_state_manifest_paths)
    publish_result = (
        resolve_substack_publish_from_manifest(effect, publish_manifest_path)
        if publish_manifest_path is not None
        else None
    )
    rss_result = resolve_rss_publish_from_feeds(effect, rss_feed_paths) if rss_feed_paths else None
    result = resolve_effect_from_provider_state_manifests(effect, manifest_paths) if manifest_paths else None
    return {
        "replay_bundle_provider_evidence": "present" if replay_bundle.get("provider_evidence") else "missing",
        "publish_manifest": {
            "path": str(publish_manifest_path) if publish_manifest_path is not None else "",
            "checked": 1 if publish_manifest_path is not None else 0,
            "result": _provider_result_summary(publish_result),
        },
        "rss_feeds": {
            "checked": len(rss_feed_paths),
            "paths": [str(path) for path in rss_feed_paths],
            "result": _provider_result_summary(rss_result),
        },
        "provider_state": {
            "directory": str(provider_state_dir),
            "checked": len(manifest_paths),
            "paths": [str(path) for path in manifest_paths],
            "result": _provider_result_summary(result),
        },
    }


def _provider_state_paths(provider_state_dir: Path, explicit_paths: list[Path | str]) -> list[Path]:
    paths = sorted(provider_state_dir.glob("*.json")) if provider_state_dir.exists() else []
    paths.extend(Path(path) for path in explicit_paths)
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def _provider_result_summary(result: ReconciliationResult | None) -> dict[str, Any]:
    if result is None:
        return {"status": "no_match"}
    return {
        "status": "proven_succeeded" if result.succeeded else "proven_failed",
        "detail": result.detail,
        "external_ref": result.external_ref or "",
        "reconciliation_ref": result.reconciliation_ref,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect a V3 effect reconciliation queue item without mutating state."
    )
    parser.add_argument("--root", type=Path, default=ROOT, help="Mira workspace root.")
    parser.add_argument("--effect-id", default="", help="Effect log id to inspect.")
    parser.add_argument("--idempotency-key", default="", help="Effect idempotency key to inspect.")
    parser.add_argument("--publish-manifest", type=Path, help="Optional Substack publication manifest to inspect.")
    parser.add_argument(
        "--rss-feed", action="append", type=Path, default=[], help="Optional RSS feed file to inspect. Can be repeated."
    )
    parser.add_argument(
        "--provider-state-dir", type=Path, help="Provider-state manifest directory. Defaults to data/v3/provider_state."
    )
    parser.add_argument(
        "--provider-state-manifest",
        action="append",
        type=Path,
        default=[],
        help="Optional provider-state JSON manifest to inspect. Can be repeated.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    try:
        payload = inspect_effect(
            root=args.root,
            effect_id=args.effect_id,
            idempotency_key=args.idempotency_key,
            publish_manifest_path=args.publish_manifest,
            rss_feed_paths=args.rss_feed,
            provider_state_dir=args.provider_state_dir,
            provider_state_manifest_paths=args.provider_state_manifest,
        )
    except (KeyError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_effect_inspection(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
