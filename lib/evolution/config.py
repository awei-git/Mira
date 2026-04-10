"""Evolution package paths and constants."""
from pathlib import Path

from config import MIRA_ROOT; _AGENTS_DIR = MIRA_ROOT / "agents"
SOUL_DIR = _AGENTS_DIR / "shared" / "soul"
EXPERIENCE_DIR = SOUL_DIR / "experiences"
LESSON_DIR = SOUL_DIR / "lessons"
VARIANT_DIR = SOUL_DIR / "variants"

# Reward signal weights for composite scoring.
# Positive = good outcome, negative = bad outcome.
# Magnitudes reflect how strongly each signal should influence learning.
REWARD_WEIGHTS = {
    # External engagement (strongest signal — real humans reacted)
    "likes": 2.0,
    "comments": 5.0,       # someone cared enough to reply
    "restacks": 3.0,
    "views": 0.01,          # views alone are weak

    # User (WA) feedback (very strong — direct human judgment)
    "wa_positive": 10.0,
    "wa_negative": -15.0,
    "wa_repeated_failure": -25.0,   # "why is this still broken?"

    # Execution outcome
    "success": 1.0,
    "failure": -3.0,
    "timeout": -2.0,
}
