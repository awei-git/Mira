"""Configuration constants for the writing pipeline."""
from pathlib import Path
import sys

# Ensure shared config is importable
_shared_dir = str(Path(__file__).resolve().parent.parent / "shared")
if _shared_dir not in sys.path:
    sys.path.insert(0, _shared_dir)

from config import WRITINGS_OUTPUT_DIR, CLAUDE_BIN as _CLAUDE_BIN

# Base paths — writer agent directory (resources live here now)
WRITINGS_ROOT = Path(__file__).resolve().parent
IDEAS_DIR = WRITINGS_ROOT / "ideas"
PROJECTS_DIR = WRITINGS_OUTPUT_DIR
FRAMEWORKS_DIR = WRITINGS_ROOT / "frameworks"
TEMPLATES_DIR = WRITINGS_ROOT / "templates"
CHECKLISTS_DIR = WRITINGS_ROOT / "checklists"
LOGS_DIR = WRITINGS_ROOT / "logs"

# Claude CLI
CLAUDE_BIN = _CLAUDE_BIN
CLAUDE_TIMEOUT = 1200  # seconds per invocation (20 min — revision needs more than drafting)
CLAUDE_MAX_RETRIES = 2

# How many steps to advance per idea per daily run
# 3 = scaffold + draft + critique in one run
MAX_STEPS_PER_RUN = 3

# Type-to-framework mapping
TYPE_FRAMEWORK = {
    "essay": "essay.md",
    "novel": "novel.md",
    "blog": "blog.md",
}

# Type-to-scaffold mapping (which templates to copy per project type)
TYPE_SCAFFOLD = {
    "essay": {
        "templates": {
            "规格.md": "project-spec.md",
            "大纲.md": "essay-outline.md",
        },
        "dirs": ["drafts", "final"],
    },
    "novel": {
        "templates": {
            "规格.md": "project-spec.md",
            "章节.md": "chapter-card.md",
            "描述.md": "character-sheet.md",
            "修改.md": "editorial-review.md",
        },
        "dirs": ["drafts", "submission"],
    },
    "blog": {
        "templates": {
            "规格.md": "project-spec.md",
            "大纲.md": "essay-outline.md",
        },
        "dirs": ["drafts", "final"],
    },
}

# Chinese type aliases → canonical English type
TYPE_ALIASES = {
    "博客": "blog",
    "散文": "essay",
    "随笔": "essay",
    "小说": "novel",
    "短篇小说": "novel",
    "长篇小说": "novel",
    "短篇": "novel",
}

FEEDBACK_FILENAME = "feedback.md"

# Apple Notes sync
NOTES_FOLDER_NAME = "写作想法"
NOTES_SYNC_STATE = IDEAS_DIR / ".notes_sync.json"
