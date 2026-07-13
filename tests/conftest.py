"""Pytest configuration — centralized sys.path setup for all tests.

Adds lib/ (shared modules) and agents/super/ (core agent modules).
Individual agent dirs are added by per-directory conftest.py files so that
``import handler`` resolves to the correct agent's handler.py.
"""

import sys
from pathlib import Path

MIRA_ROOT = Path(__file__).resolve().parent.parent
_AGENTS = MIRA_ROOT / "agents"

# lib/ has all shared modules (config, memory, ops, evolution, etc.)
_LIB = str(MIRA_ROOT / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

# super/ for core.py, task_manager.py, health_monitor.py etc.
_SUPER = str(_AGENTS / "super")
if _SUPER not in sys.path:
    sys.path.insert(0, _SUPER)

# Exclude one-off scripts that aren't real tests
collect_ignore_glob = ["**/test_audio_upload.py"]
