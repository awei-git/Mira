"""Configuration for the Mira agent system.

Reads from config.yml at the project root. Falls back to defaults.
"""
import json
import re
from pathlib import Path
from datetime import time

# ---------------------------------------------------------------------------
# Load config.yml  (stdlib-only parser — no PyYAML dependency)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # agents/shared/ -> agents/ -> Mira/
_CONFIG_FILE = _PROJECT_ROOT / "config.yml"

def _parse_simple_yaml(text: str) -> dict:
    """Parse the subset of YAML used in config.yml (scalar values, one-level
    nesting, inline lists).  Avoids a PyYAML dependency so launchd works."""
    result: dict = {}
    current_section = None
    for raw_line in text.splitlines():
        # strip comments (but not inside quoted strings)
        line = raw_line.split("#")[0].rstrip() if "#" in raw_line and not re.search(r'["\'].*#.*["\']', raw_line) else raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()
        # Remove surrounding quotes
        if val and val[0] in ('"', "'") and val[-1] == val[0]:
            val = val[1:-1]
        if indent == 0:
            if val == "" or val == "":
                # section header
                current_section = key
                result[key] = {}
            elif val.startswith("["):
                # inline list like ["a", "b"]
                try:
                    result[key] = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    result[key] = val
                current_section = None
            else:
                # Try to coerce to bool, then int
                if val.lower() in ('true', 'false'):
                    result[key] = val.lower() == 'true'
                else:
                    try:
                        result[key] = int(val)
                    except ValueError:
                        result[key] = val
                current_section = None
        elif current_section and indent > 0:
            if val.startswith("["):
                try:
                    result[current_section][key] = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    result[current_section][key] = val
            else:
                if val.lower() in ('true', 'false'):
                    result[current_section][key] = val.lower() == 'true'
                else:
                    try:
                        result[current_section][key] = int(val)
                    except ValueError:
                        result[current_section][key] = val
    return result

def _load_config() -> dict:
    if _CONFIG_FILE.exists():
        try:
            return _parse_simple_yaml(_CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            import logging
            logging.getLogger("mira.config").warning("Failed to load config.yml: %s", e)
            return {}
    return {}

_cfg = _load_config()

# ---------------------------------------------------------------------------
# Base paths (from config.yml or auto-detect)
# ---------------------------------------------------------------------------
_root_str = _cfg.get("root_path", str(_PROJECT_ROOT))
MIRA_ROOT = Path(_root_str).expanduser()
_AGENTS_DIR = MIRA_ROOT / "agents"


# Soul — shared identity, memory, interests
SOUL_DIR = _AGENTS_DIR / "shared" / "soul"

LOGS_DIR = MIRA_ROOT / "logs"
FEEDS_DIR = MIRA_ROOT / "feeds"
SOURCES_FILE = MIRA_ROOT / "sources.json"

# Artifacts — unified output folder (browsable from Mira app)
ARTIFACTS_DIR = MIRA_ROOT / "artifacts"
BRIEFINGS_DIR = ARTIFACTS_DIR / "briefings"
WRITINGS_OUTPUT_DIR = ARTIFACTS_DIR / "writings"
RESEARCH_DIR = ARTIFACTS_DIR / "research"

# Legacy aliases
WORKSPACE_DIR = RESEARCH_DIR

# Apple Notes inbox/outbox (under super agent)
INBOX_DIR = _AGENTS_DIR / "super" / "notes_inbox"
OUTBOX_DIR = _AGENTS_DIR / "super" / "notes_outbox"

# Writing resources (frameworks, templates, ideas — lives under writer agent)
WRITINGS_DIR = _AGENTS_DIR / "writer"

# Soul files
IDENTITY_FILE = SOUL_DIR / "identity.md"
MEMORY_FILE = SOUL_DIR / "memory.md"
INTERESTS_FILE = SOUL_DIR / "interests.md"
SKILLS_FILE = SOUL_DIR / "skills.md"

# Worldview — evolving values and beliefs
WORLDVIEW_FILE = SOUL_DIR / "worldview.md"

# Reading notes — personal reflections from deep dives
READING_NOTES_DIR = SOUL_DIR / "reading_notes"

# Conversation archive — full conversation content indexed for recall across sessions
CONVERSATIONS_DIR = SOUL_DIR / "conversations"

# Episode archive — complete task conversations saved for long-term recall
EPISODES_DIR = SOUL_DIR / "episodes"

# Content catalog — structured metadata for all produced content (articles, papers, audio, video)
CATALOG_FILE = SOUL_DIR / "catalog.jsonl"

# Dynamically learned skills
SKILLS_DIR = SOUL_DIR / "learned"
SKILLS_INDEX = SKILLS_DIR / "index.json"

# Self-evaluation scores
SCORES_FILE = SOUL_DIR / "scores.json"

# State tracking
STATE_FILE = MIRA_ROOT / ".agent_state.json"
NOTES_SYNC_STATE = INBOX_DIR / ".sync.json"

# Mira-bridge — file-based iPhone <-> Mac messaging over iCloud Drive
MIRA_BRIDGE_DIR = MIRA_ROOT / "Mira-bridge"

# Artifacts — iOS reads from here (Mira/artifacts/, NOT Mira-bridge/artifacts/)
ARTIFACTS_DIR = MIRA_ROOT / "artifacts"

# Legacy aliases
MIRA_DIR = MIRA_BRIDGE_DIR
TALKBRIDGE_DIR = MIRA_BRIDGE_DIR

# ---------------------------------------------------------------------------
# Apple Notes folders (from config.yml)
# ---------------------------------------------------------------------------
_notes_cfg = _cfg.get("notes", {})
NOTES_INBOX_FOLDER = _notes_cfg.get("inbox_folder", "Mira")
NOTES_BRIEFING_FOLDER = _notes_cfg.get("briefing_folder", "Mira Briefings")
NOTES_OUTPUT_FOLDER = _notes_cfg.get("output_folder", "Mira Results")

# ---------------------------------------------------------------------------
# Claude CLI
# ---------------------------------------------------------------------------
CLAUDE_BIN = _cfg.get("claude_bin", "/opt/homebrew/bin/claude")
CLAUDE_TIMEOUT_THINK = 120   # seconds for simple calls (classify, filter)
CLAUDE_TIMEOUT_PLAN = 300    # seconds for medium calls (analyze, review, plan)
CLAUDE_TIMEOUT_ACT = 600     # seconds for complex calls (write, code, research)

_limits = _cfg.get("limits", {})
TASK_TIMEOUT = _limits.get("task_timeout", 600)
TASK_TIMEOUT_LONG = _limits.get("task_timeout_long", 3600)  # writing pipeline, research
CLEANUP_DAYS = _limits.get("cleanup_days", 3)

# Secrets file (API keys — always gitignored)
SECRETS_FILE = _PROJECT_ROOT / "secrets.yml"

# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------
MODELS = {
    "claude": {
        "provider": "claude",
        "model_id": "claude",
        "style": "Precise, structured, follows instructions well",
    },
    "gpt5": {
        "provider": "openai",
        "model_id": "gpt-5.4",
        "style": "Creative, fluent, good at natural-sounding prose",
    },
    "deepseek": {
        "provider": "deepseek",
        "model_id": "deepseek-chat",
        "style": "Strong reasoning, good at Chinese writing, cost-efficient",
    },
    "deepseek-r1": {
        "provider": "deepseek",
        "model_id": "deepseek-reasoner",
        "style": "Deep chain-of-thought reasoning, best for complex analysis",
    },
    "gemini": {
        "provider": "gemini",
        "model_id": "gemini-3.1-flash-lite-preview",
        "style": "Fast, multimodal, good at long-context and structured output",
    },
    "gemini-pro": {
        "provider": "gemini",
        "model_id": "gemini-3.1-pro-preview",
        "style": "Most capable Gemini, strong reasoning and code generation",
    },
    "codex": {
        "provider": "openai",
        "model_id": "gpt-5.4",
        "style": "OpenAI GPT-5.4 — used as Claude fallback when quota is hit",
    },
}

# Which models to use for writing tasks (from config.yml)
_models_cfg = _cfg.get("models", {})
WRITING_MODELS = _models_cfg.get("writing", ["claude", "gpt5", "deepseek", "gemini"])
REVIEW_MODELS = _models_cfg.get("review", ["claude", "gpt5", "gemini"])
DEFAULT_MODEL = _models_cfg.get("default", "claude")
CLAUDE_FALLBACK_MODEL = _models_cfg.get("claude_fallback", "codex")

# Publishing controls
_publishing_cfg = _cfg.get("publishing", {})
SUBSTACK_PUBLISHING_DISABLED = _publishing_cfg.get("substack_disabled", False)

# Writing workflow
MIN_REVIEW_ROUNDS = 5

# Assessment criteria by writing type
WRITING_CRITERIA = {
    "novel": {
        "name": "小说",
        "criteria": {
            "主题深度": "Theme depth — profundity and resonance",
            "设定合理性": "World-building — internal consistency and richness",
            "人物塑造": "Characterization — depth, believability, growth arcs",
            "故事结构": "Plot structure — pacing, arc, coherence",
            "节奏感": "Flow — rhythm, tension and release, page-turning quality",
            "语言风格": "Language — prose quality, voice, word choice",
            "情节转折": "Plot twists — surprise, foreshadowing, emotional impact",
        },
    },
    "essay": {
        "name": "散文/随笔",
        "criteria": {
            "论点清晰度": "Thesis clarity — is the main argument clear and compelling?",
            "论证逻辑": "Argumentation — logical flow, evidence quality, persuasiveness",
            "素材丰富度": "Material richness — examples, references, depth of research",
            "可读性": "Readability — engagement, accessibility, hooking the reader",
            "原创性": "Originality — fresh perspective, unique insight or voice",
            "结构完整性": "Structure — intro/body/conclusion coherence, transitions",
            "语言表达": "Language — precision, elegance, appropriate tone",
        },
    },
    "blog": {
        "name": "博客",
        "criteria": {
            "标题吸引力": "Title/hook — does it grab attention?",
            "观点鲜明度": "Point of view — clear, strong, memorable stance",
            "可读性": "Readability — scannable, well-formatted, accessible",
            "实用价值": "Practical value — actionable insights, takeaways",
            "原创性": "Originality — unique angle or fresh insight",
            "结构流畅度": "Structure — logical flow, good transitions",
            "语言风格": "Voice — appropriate tone, engaging and natural style",
        },
    },
    "technical": {
        "name": "技术文章",
        "criteria": {
            "准确性": "Accuracy — factually correct, technically precise",
            "清晰度": "Clarity — easy to follow, well-explained concepts",
            "完整性": "Completeness — covers all necessary aspects",
            "实用性": "Practicality — useful, applicable, with working examples",
            "代码质量": "Code quality — correct, idiomatic, well-commented",
            "结构合理性": "Structure — logical organization, good progression",
            "深度": "Depth — thorough analysis, not superficial",
        },
    },
    "poetry": {
        "name": "诗歌",
        "criteria": {
            "意境": "Imagery/mood — atmospheric quality, evocative power",
            "韵律": "Rhythm/meter — musicality, sonic quality",
            "意象": "Symbolism — layered meaning, metaphor quality",
            "情感表达": "Emotional expression — authenticity, resonance",
            "语言凝练度": "Concision — economy of language, every word counts",
            "创新性": "Innovation — fresh language, unexpected connections",
        },
    },
}

# Journal (daily summary)
JOURNAL_DIR = SOUL_DIR / "journal"

# ---------------------------------------------------------------------------
# Schedule (from config.yml)
# ---------------------------------------------------------------------------
_sched = _cfg.get("schedule", {})

def _parse_times(time_strs: list[str]) -> list[time]:
    result = []
    for s in time_strs:
        h, m = s.split(":")
        result.append(time(int(h), int(m)))
    return result

# Explore: free-form, curiosity-driven
# Source groups are a pool — each explore session picks one at random (LRU-weighted)
_explore_source_groups_raw = _sched.get("explore_source_groups",
    _sched.get("explore_slot_sources",  # backward compat with old config
               ["arxiv,huggingface", "reddit,ai_news",
                "github_trending,hackernews,lobsters",
                "quanta_magazine,aeon_essays,stanford_encyclopedia,marginal_revolution,astral_codex_ten",
                "literaryhub,brain_pickings,3blue1brown,veritasium",
                "noah_smith,stratechery,lennys_newsletter,the_economist,matt_levine"]))
EXPLORE_SOURCE_GROUPS = [g.split(",") for g in _explore_source_groups_raw]
# Skill study: dedicated source groups for learning craft skills (video, photo)
SKILL_STUDY_SOURCE_GROUPS = [
    {
        "domain": "video",
        "sources": ["r/videoediting", "r/editors", "r/colorgrading", "r/cinematography",
                     "film_riot", "corridor_crew", "casey_neistat", "gerald_undone",
                     "every_frame_a_painting"],
        "skill_dir": "video",
    },
    {
        "domain": "photo",
        "sources": ["r/postprocessing", "r/photocritique", "r/photography",
                     "peter_mckinnon", "daniel_schiffer", "phlearn"],
        "skill_dir": "photo",
    },
]
SKILL_STUDY_COOLDOWN_HOURS = _sched.get("skill_study_cooldown_hours", 20)  # ~once per day
SKILL_STUDY_TIME = _parse_times([_sched.get("skill_study_time", "14:00")])[0]

EXPLORE_COOLDOWN_MINUTES = _sched.get("explore_cooldown_minutes",
    _sched.get("explore_window_minutes", 90))  # default 90min between explores
EXPLORE_ACTIVE_START = _parse_times([_sched.get("explore_start", "08:00")])[0]
EXPLORE_ACTIVE_END = _parse_times([_sched.get("explore_end", "23:00")])[0]
# Max explores per day (safety valve)
EXPLORE_MAX_PER_DAY = _sched.get("explore_max_per_day", 8)

REFLECT_DAY = _sched.get("reflect_day", 6)
REFLECT_TIME = _parse_times([_sched.get("reflect_time", "10:00")])[0]
JOURNAL_TIME = _parse_times([_sched.get("journal_time", "23:00")])[0]

# Analyst: dual schedule (pre-market + post-market)
_analyst_raw = _sched.get("analyst_times", _sched.get("analyst_time", ["08:30"]))
if isinstance(_analyst_raw, str):
    _analyst_raw = [_analyst_raw]
ANALYST_TIMES = _parse_times(_analyst_raw)
ANALYST_BUSINESS_DAYS_ONLY = _sched.get("analyst_business_days_only", True)

# Daily philosophical thought
ZHESI_TIME = _parse_times([_sched.get("zhesi_time", "09:30")])[0]

# 杂.md — philosophical fragments for mining
ZA_FILE = WRITINGS_DIR / "ideas" / "杂.md"

# Limits
MAX_FEED_ITEMS = _limits.get("max_feed_items", 50)
MAX_BRIEFING_ITEMS = _limits.get("max_briefing_items", 7)
MAX_DEEP_DIVES = _limits.get("max_deep_dives", 1)
MAX_MEMORY_LINES = _limits.get("max_memory_lines", 200)
