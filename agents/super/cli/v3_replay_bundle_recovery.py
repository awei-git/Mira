"""Recover replay bundle refs for legacy V3 effect-log rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mira.engine import EffectLog, recover_missing_replay_bundles
from mira.runtime import default_v3_paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover replay bundles for legacy V3 effect-log rows.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--dry-run", action="store_true", help="Report recoverable rows without writing bundles.")
    args = parser.parse_args()

    paths = default_v3_paths(ROOT)
    results = recover_missing_replay_bundles(
        EffectLog(paths.effect_log),
        artifact_root=paths.root,
        checkpoint_dir=paths.checkpoints,
        provider_state_dir=paths.provider_state_manifests,
        dry_run=args.dry_run,
    )
    payload = {"dry_run": args.dry_run, "recovered": [item.to_dict() for item in results]}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        action = "would recover" if args.dry_run else "recovered"
        print(f"{action} {len(results)} replay bundles")
        for item in results:
            print(f"- {item.idempotency_key}: {item.replay_bundle_ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
