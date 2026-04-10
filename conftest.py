"""Pytest configuration — set up sys.path for all agent test modules.

Only add *shared* directories (shared, super) here.  Individual agent dirs
(coder, evaluator, …) are added by each test file so that ``import handler``
resolves to the correct agent's handler.py, not whichever one was cached first.
"""
import sys
from pathlib import Path

_AGENTS = Path(__file__).resolve().parent / "agents"

# lib/ has all shared modules (moved from agents/shared/)
_LIB = str(Path(__file__).resolve().parent / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

# super/ for core.py, task_manager.py etc.
_SUPER = str(_AGENTS / "super")
if _SUPER not in sys.path:
    sys.path.insert(0, _SUPER)

# Exclude one-off scripts that aren't real tests
collect_ignore_glob = ["**/test_audio_upload.py"]
