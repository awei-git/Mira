"""Centralized sys.path setup for Mira agent system.

Import this module once at any entry point to ensure all Mira packages
are importable without scattered sys.path.insert() calls.

Usage (at the top of any entry point script):
    import pathsetup  # noqa: F401  — side-effect import
"""

import sys
from pathlib import Path

_MIRA_ROOT = Path(__file__).resolve().parent.parent  # ~/Sandbox/Mira/

_PATHS = [
    str(_MIRA_ROOT / "lib"),
    str(_MIRA_ROOT / "agents" / "shared"),
    str(_MIRA_ROOT / "agents" / "super"),
    str(_MIRA_ROOT / "agents" / "writer"),
    str(_MIRA_ROOT / "agents" / "explorer"),
    str(_MIRA_ROOT / "agents" / "socialmedia"),
    str(_MIRA_ROOT / "agents" / "podcast"),
    str(_MIRA_ROOT / "agents" / "video"),
    str(_MIRA_ROOT / "agents" / "photo"),
    str(_MIRA_ROOT / "agents" / "surfer"),
    str(_MIRA_ROOT / "agents" / "researcher"),
    str(_MIRA_ROOT / "agents" / "evaluator"),
    str(_MIRA_ROOT / "agents" / "reader"),
    str(_MIRA_ROOT / "agents" / "general"),
    str(_MIRA_ROOT / "agents" / "health"),
    str(_MIRA_ROOT / "agents" / "analyst"),
    str(_MIRA_ROOT / "agents" / "coder"),
]

for p in _PATHS:
    if p not in sys.path:
        sys.path.insert(0, p)
