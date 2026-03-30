"""Pytest configuration — set up sys.path for all agent test modules."""
import sys
from pathlib import Path

_AGENTS = Path(__file__).resolve().parent / "agents"

# Add all agent directories and shared to sys.path
for subdir in ["shared", "super", "writer", "general", "podcast",
               "socialmedia", "explorer", "coder", "secret", "evaluator",
               "researcher", "photo", "video", "analyst"]:
    p = str(_AGENTS / subdir)
    if p not in sys.path:
        sys.path.insert(0, p)

# Exclude one-off scripts that aren't real tests
collect_ignore_glob = ["**/test_audio_upload.py"]
