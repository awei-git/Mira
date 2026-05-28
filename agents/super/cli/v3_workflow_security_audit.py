#!/usr/bin/env python3
"""Audit all V3.1 workflow packs and skills before enablement."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mira.workflows import audit_workflow_tree


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit every workflow command and skill under workflow_packs.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Workspace root.")
    parser.add_argument("--workflow-root", type=Path, help="Workflow pack root. Defaults to <root>/workflow_packs.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()

    workflow_root = args.workflow_root or args.root / "workflow_packs"
    result = audit_workflow_tree(workflow_root)
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        audit = payload["workflow_tree_audit"]
        print(f"Workflow security audit: {audit['result']}")
        print(f"Targets: {audit['target_count']}")
        print(f"Files checked: {audit['files_checked_count']}")
        print(f"Findings: {audit['finding_count']}")
        for finding in audit["findings"]:
            print(f"- {finding['file']}: {finding['check']}: {finding['reason']} ({finding['pattern']})")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
