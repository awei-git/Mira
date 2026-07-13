"""Add agent-specific directories to sys.path."""

import sys
from pathlib import Path

_AGENTS = Path(__file__).resolve().parent.parent.parent / "agents"
_p = str(_AGENTS / "coder")
if _p not in sys.path:
    sys.path.insert(0, _p)
_p = str(_AGENTS / "super")
if _p not in sys.path:
    sys.path.insert(0, _p)
