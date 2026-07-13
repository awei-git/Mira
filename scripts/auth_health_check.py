#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

_LIB_DIR = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from auth_health import run_auth_health_checks


def main() -> int:
    results = run_auth_health_checks()
    worst = 0
    for result in results:
        print(f"{result.provider}: {result.status} [{result.severity}] {result.detail}")
        if result.severity == "critical":
            worst = 2
        elif result.severity == "warning" and worst < 1:
            worst = 1
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
