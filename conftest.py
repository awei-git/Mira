"""Pytest configuration — set up sys.path for all agent test modules.

Only add *shared* directories (shared, super) here.  Individual agent dirs
(coder, evaluator, …) are added by each test file so that ``import handler``
resolves to the correct agent's handler.py, not whichever one was cached first.
"""
import sys
from pathlib import Path

_AGENTS = Path(__file__).resolve().parent / "agents"

# Shared modules only — never add individual agent dirs here to avoid
# handler.py module-cache collisions across test files.
for subdir in ["shared", "super"]:
    p = str(_AGENTS / subdir)
    if p not in sys.path:
        sys.path.insert(0, p)

# Exclude one-off scripts that aren't real tests
collect_ignore_glob = ["**/test_audio_upload.py"]
