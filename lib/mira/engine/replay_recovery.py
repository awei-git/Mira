"""Recover replay bundle refs for legacy effect-log rows."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from mira.engine.effect_log import EffectLog, EffectLogEntry
from mira.kernel.schema import utc_now


IMPORTANT_EFFECT_RE = re.compile(
    r"(publish|post|tweet|upload|rss|substack|compact|archive|memory|delete|rollback|promote|deploy|production)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReplayBundleRecoveryResult:
    idempotency_key: str
    status: str
    replay_bundle_ref: str
    payload_reconstructable: bool
    sources: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def recover_missing_replay_bundles(
    effect_log: EffectLog,
    *,
    artifact_root: Path | str,
    checkpoint_dir: Path | str,
    provider_state_dir: Path | str | None = None,
    dry_run: bool = False,
) -> list[ReplayBundleRecoveryResult]:
    results: list[ReplayBundleRecoveryResult] = []
    for effect in _latest_effects(effect_log.list()):
        if not _needs_replay_recovery(effect):
            continue
        bundle, payload_reconstructable, sources = _build_recovery_bundle(
            effect,
            checkpoint_dir=Path(checkpoint_dir),
            provider_state_dir=Path(provider_state_dir) if provider_state_dir is not None else None,
        )
        path = _recovery_bundle_path(Path(artifact_root), effect)
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(bundle, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
            effect_log.attach_replay_bundle(
                effect.idempotency_key,
                str(path),
                detail=effect.detail or "replay bundle recovered from existing durable evidence",
            )
        results.append(
            ReplayBundleRecoveryResult(
                idempotency_key=effect.idempotency_key,
                status=effect.status,
                replay_bundle_ref=str(path),
                payload_reconstructable=payload_reconstructable,
                sources=sources,
            )
        )
    return results


def _latest_effects(effects: list[EffectLogEntry]) -> list[EffectLogEntry]:
    latest: dict[str, EffectLogEntry] = {}
    for effect in effects:
        latest[effect.idempotency_key] = effect
    return list(latest.values())


def _needs_replay_recovery(effect: EffectLogEntry) -> bool:
    if effect.replay_bundle_ref:
        return False
    if effect.status not in {
        "planned",
        "executing",
        "started",
        "succeeded",
        "failed",
        "unknown",
        "reconciled_succeeded",
        "reconciled_failed",
    }:
        return False
    text = " ".join([effect.action, effect.action_type, effect.step_id, effect.idempotency_key])
    return bool(IMPORTANT_EFFECT_RE.search(text))


def _build_recovery_bundle(
    effect: EffectLogEntry,
    *,
    checkpoint_dir: Path,
    provider_state_dir: Path | None,
) -> tuple[dict[str, Any], bool, list[str]]:
    sources: list[str] = []
    checkpoint = _load_checkpoint(checkpoint_dir, effect.run_id)
    provider_evidence = _provider_state_evidence(effect, provider_state_dir)
    payload = _payload_from_provider(provider_evidence) or _payload_from_checkpoint(effect, checkpoint)
    artifact_refs = _artifact_refs(checkpoint)
    if checkpoint:
        sources.append(f"checkpoint:{checkpoint_dir / (effect.run_id + '.json')}")
    if provider_evidence:
        sources.append(str(provider_evidence.get("_source_ref", "provider_state")))
    sources.extend(f"artifact:{path}" for path in artifact_refs)
    payload_reconstructable = payload is not None
    payload_for_hash = payload or {"target": effect.target, "idempotency_key": effect.idempotency_key}
    bundle = {
        "bundle_version": "v3.1-recovered",
        "recovered_at": utc_now().isoformat(),
        "recovery_note": (
            "Recovered from durable checkpoint/provider-state evidence. "
            "This preserves replay and compensation context for a legacy effect row that predates replay_bundle_ref."
        ),
        "payload_reconstructable": payload_reconstructable,
        "run_id": effect.run_id,
        "pipeline": effect.pipeline,
        "step_id": effect.step_id or effect.action,
        "action_type": effect.action_type or effect.action,
        "target": effect.target,
        "idempotency_key": effect.idempotency_key,
        "preview_hash": effect.preview_hash,
        "approval_token_id": effect.approval_token_id,
        "payload_hash": _payload_hash(payload_for_hash),
        "payload": _redact(payload_for_hash),
        "effect": effect.to_dict(),
        "provider_evidence": provider_evidence,
        "artifact_refs": artifact_refs,
        "sources": sources,
        "compensation": _compensation_for_action(effect.action_type or effect.action),
    }
    return bundle, payload_reconstructable, sources


def _load_checkpoint(checkpoint_dir: Path, run_id: str) -> dict[str, Any] | None:
    path = checkpoint_dir / f"{run_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _provider_state_evidence(effect: EffectLogEntry, provider_state_dir: Path | None) -> dict[str, Any] | None:
    candidates: list[Path] = []
    if effect.reconciliation_ref and effect.reconciliation_ref.startswith("provider_state:"):
        ref_path = effect.reconciliation_ref.removeprefix("provider_state:").split(":", 1)[0]
        candidates.append(Path(ref_path))
    if provider_state_dir is not None and provider_state_dir.exists():
        candidates.extend(sorted(provider_state_dir.glob("*.json")))
    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        match = _find_provider_entry(data, effect)
        if match is not None:
            match = dict(match)
            match["_source_ref"] = f"provider_state:{path}"
            return match
    return None


def _find_provider_entry(value: Any, effect: EffectLogEntry) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if value.get("idempotency_key") == effect.idempotency_key or value.get("target") == effect.target:
            return value
        for item in value.values():
            found = _find_provider_entry(item, effect)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_provider_entry(item, effect)
            if found is not None:
                return found
    return None


def _payload_from_provider(provider_evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    if not provider_evidence:
        return None
    preview = provider_evidence.get("preview")
    return preview if isinstance(preview, dict) else None


def _payload_from_checkpoint(effect: EffectLogEntry, checkpoint: dict[str, Any] | None) -> dict[str, Any] | None:
    if not checkpoint:
        return None
    outputs = checkpoint.get("outputs") or {}
    if not isinstance(outputs, dict):
        return None
    for key in (effect.step_id, effect.action, "publish_substack_idempotent", "post_note_idempotent"):
        if key and isinstance(outputs.get(key), dict):
            payload = {k: v for k, v in outputs[key].items() if not str(k).startswith("_")}
            if payload:
                return payload
    draft = outputs.get("draft")
    if isinstance(draft, dict):
        payload = {k: draft[k] for k in ("title", "draft") if k in draft}
        if payload:
            return payload
    return None


def _artifact_refs(checkpoint: dict[str, Any] | None) -> list[str]:
    outputs = (checkpoint or {}).get("outputs") or {}
    refs = outputs.get("_artifacts", []) if isinstance(outputs, dict) else []
    return [str(ref) for ref in refs]


def _recovery_bundle_path(artifact_root: Path, effect: EffectLogEntry) -> Path:
    safe_key = hashlib.sha256(effect.idempotency_key.encode("utf-8")).hexdigest()[:16]
    step = effect.step_id or effect.action
    return artifact_root / "effect_replay_bundles" / f"{effect.run_id}-{step}-{safe_key}-recovered.json"


def _payload_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in ("secret", "token", "password", "api_key", "authorization")):
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _compensation_for_action(action_type: str) -> dict[str, str]:
    lowered = action_type.lower()
    if "publish" in lowered or "post" in lowered:
        return {
            "strategy": "unpublish_or_mark_retracted",
            "rollback_note": "use provider reconciliation ref to remove or mark the public artifact as corrected",
        }
    if "email" in lowered or "send" in lowered:
        return {
            "strategy": "impossible",
            "rollback_note": "send follow-up correction; the original send cannot be unsent",
        }
    if "file" in lowered:
        return {"strategy": "restore_backup", "rollback_note": "restore the recorded pre-write backup"}
    if "memory" in lowered or "compact" in lowered:
        return {"strategy": "rollback_to_snapshot", "rollback_note": "restore from rollback pointer or archived memory"}
    if "deploy" in lowered or "promotion" in lowered or "rollback" in lowered:
        return {"strategy": "compensating_deployment_action", "rollback_note": "run configured rollback adapter"}
    return {"strategy": "manual_reconcile", "rollback_note": "review effect log and provider state before retry"}
