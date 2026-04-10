"""Add lib/ to sys.path for all shared tests."""
import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parent.parent.parent.parent / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
