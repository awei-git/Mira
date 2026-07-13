"""Ensure lib/ is importable for schemas tests."""

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import pathsetup  # noqa: F401,E402
