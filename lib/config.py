"""Configuration for the Mira agent system.

Reads from config.yml at the project root. Falls back to defaults.
"""

import json
import logging
import os
import re
import sys
from pathlib import Path
from datetime import datetime as _datetime, time, timezone as _utc_tz

_log = logging.getLogger("mira.config")

# ---------------------------------------------------------------------------
# Local timezone — auto-detected from OS, no external dependencies
# ---------------------------------------------------------------------------
LOCAL_TZ = _datetime.now().astimezone().tzinfo


def now_local() -> _datetime:
    """Current time as a timezone-aware datetime in the system's local timezone."""
    return _datetime.now(_utc_tz.utc).astimezone(LOCAL_TZ)


def today_local() -> str:
    """Today's date string (YYYY-MM-DD) in the system's local timezone."""
    return now_local().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Load config.yml  (stdlib-only parser — no PyYAML dependency)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent  # lib/ -> Mira/
_CONFIG_FILE = _PROJECT_ROOT / "config.yml"


def _parse_simple_yaml(text: str) -> dict:
    """Parse the subset of YAML used in config.yml (scalar values, one-level
    nesting, inline lists).  Avoids a PyYAML dependency so launchd works."""
    result: dict = {}
    current_section = None
    for raw_line in text.splitlines():
        # strip comments (but not inside quoted strings)
        line = (
            raw_line.split("#")[0].rstrip()
            if "#" in raw_line and not re.search(r'["\'].*#.*["\']', raw_line)
            else raw_line.rstrip()
        )
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
            if val == "":
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
                if val.lower() in ("true", "false"):
                    result[key] = val.lower() == "true"
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
                if val.lower() in ("true", "false"):
                    result[current_section][key] = val.lower() == "true"
                else:
                    try:
                        result[current_section][key] = int(val)
                    except ValueError:
                        result[current_section][key] = val
    return result


def _load_config() -> dict:
    if _CONFIG_FILE.exists():
        try:
            import yaml

            return yaml.safe_load(_CONFIG_FILE.read_text(encoding="utf-8")) or {}
        except ImportError:
            pass
        except Exception as e:
            import logging

            logging.getLogger("mira.config").warning("PyYAML failed: %s, trying simple parser", e)
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

# ---------------------------------------------------------------------------
# Centralized data directory — all runtime data lives under data/
# ---------------------------------------------------------------------------
DATA_DIR = MIRA_ROOT / "data"

# Soul — shared identity, memory, interests
SOUL_DIR = DATA_DIR / "soul"

LOGS_DIR = DATA_DIR / "logs"
TIMING_LOG = LOGS_DIR / "timing.jsonl"
FEEDS_DIR = DATA_DIR / "feeds"
SOURCES_FILE = MIRA_ROOT / "sources.json"

# Artifacts — subdirectory definitions deferred until iCloud override is applied (see below)

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

# Knowledge changelog — append-only audit trail for all soul mutations
CHANGELOG_FILE = SOUL_DIR / "changelog.md"
CHANGELOG_ARCHIVE_DIR = SOUL_DIR / "changelog_archive"
CHANGELOG_MAX_LINES = 2000

# State tracking — centralized under data/state/
STATE_DIR = DATA_DIR / "state"
STATE_FILE = STATE_DIR / "agent_state.json"
SESSION_FILE = STATE_DIR / "session_context.json"
HEALTH_FILE = STATE_DIR / "bg_health.json"
PENDING_PUBLISH_FILE = STATE_DIR / "pending_publish.json"
SCHEDULED_JOBS_FILE = STATE_DIR / "scheduled_jobs.json"

# Runtime directories
PIDS_DIR = DATA_DIR / "pids"
TASKS_DIR = DATA_DIR / "tasks"
SOCIAL_STATE_DIR = DATA_DIR / "social"
PROPOSALS_DIR = DATA_DIR / "proposals"
AUTORESEARCH_DIR = DATA_DIR / "autoresearch"

# ---------------------------------------------------------------------------
# iCloud paths — bridge and artifacts live on iCloud for iOS app access
# ---------------------------------------------------------------------------
_icloud_bridge = _cfg.get("icloud_bridge_path", "")
_icloud_artifacts = _cfg.get("icloud_artifacts_path", "")

MIRA_BRIDGE_DIR = Path(_icloud_bridge).expanduser() if _icloud_bridge else MIRA_ROOT / "Mira-bridge"
ARTIFACTS_DIR = Path(_icloud_artifacts).expanduser() if _icloud_artifacts else MIRA_ROOT / "artifacts"

# Artifact subdirectories (on iCloud, browsable from iOS app)
BRIEFINGS_DIR = ARTIFACTS_DIR / "briefings"
WRITINGS_OUTPUT_DIR = ARTIFACTS_DIR / "writings"
RESEARCH_DIR = ARTIFACTS_DIR / "research"

# Personal wiki — topic-indexed knowledge base (on iCloud for iOS access)
WIKI_DIR = ARTIFACTS_DIR / "wiki"
WIKI_META = SOUL_DIR / "wiki_meta.json"
WIKI_LOG_MAX_LINES = 500
PODCAST_REPOS_DIR = MIRA_ROOT / ".podcast_repos"

# Legacy aliases
WORKSPACE_DIR = RESEARCH_DIR
MIRA_DIR = MIRA_BRIDGE_DIR
TALKBRIDGE_DIR = MIRA_BRIDGE_DIR


# ---------------------------------------------------------------------------
# Claude CLI
# ---------------------------------------------------------------------------
CLAUDE_BIN = _cfg.get("claude_bin", "/opt/homebrew/bin/claude")
# Timeouts loaded below from _timeouts section

_limits = _cfg.get("limits", {})
TASK_TIMEOUT = _limits.get("task_timeout", 900)  # Must exceed CLAUDE_TIMEOUT_ACT (600s) + startup overhead
TASK_TIMEOUT_LONG = _limits.get("task_timeout_long", 3600)  # writing pipeline, research
MAX_CONCURRENT_TASKS = _limits.get("max_concurrent_tasks", 2)  # parallel sub-agent workers
MAX_TASKS_PER_CYCLE = int(os.environ.get("MIRA_MAX_TASKS_PER_CYCLE", _limits.get("max_tasks_per_cycle", 5)))
CLEANUP_DAYS = _limits.get("cleanup_days", 3)
LOG_RETENTION_DAYS = _limits.get("log_retention_days", 14)
MAX_EXTERNAL_SKILLS_PER_DAY = _limits.get("max_external_skills_per_day", 5)
SKILL_REAUDIT_DAYS = _limits.get("skill_reaudit_days", 30)
SKILL_AUDIT_PATTERN_REVIEWED_DATE = _limits.get("skill_audit_pattern_reviewed_date", "2026-04-13")
SKILL_AUDIT_STALENESS_DAYS = _limits.get("skill_audit_staleness_days", 30)
SKILL_AUDIT_TTL_DAYS = _limits.get("skill_audit_ttl_days", 30)
SKILL_AUDIT_STRICT_MODE = _limits.get("skill_audit_strict_mode", False)
SKILL_STALENESS_DAYS = _limits.get("skill_staleness_days", 30)

# Secrets file (API keys — always gitignored)
SECRETS_FILE = _PROJECT_ROOT / "secrets.yml"

# ---------------------------------------------------------------------------
# oMLX (local LLM — privacy-safe, no network, Apple Silicon optimized)
# ---------------------------------------------------------------------------
_omlx_cfg = _cfg.get("omlx", {})
OMLX_HOST = _omlx_cfg.get("host", "127.0.0.1")
OMLX_PORT = _omlx_cfg.get("port", 8800)
OMLX_DEFAULT_MODEL = _omlx_cfg.get("default_model", "gemma-4-31b-it-4bit")
OMLX_FALLBACK_MODEL = _omlx_cfg.get("fallback_model", "Qwen3.5-27B-4bit")
OMLX_EMBED_MODEL = _omlx_cfg.get("embed_model", "nomicai-modernbert-embed-base-4bit")

# Legacy aliases — kept only for external callers; prefer OMLX_* names.
OLLAMA_HOST = OMLX_HOST
OLLAMA_PORT = OMLX_PORT
OLLAMA_DEFAULT_MODEL = OMLX_DEFAULT_MODEL
OLLAMA_EMBED_MODEL = OMLX_EMBED_MODEL

# ---------------------------------------------------------------------------
# Database (PostgreSQL — localhost only)
# ---------------------------------------------------------------------------
_db_cfg = _cfg.get("database", {})
DATABASE_URL = os.environ.get("DATABASE_URL") or _db_cfg.get("url") or "postgresql://localhost:5432/ai_system"

# ---------------------------------------------------------------------------
# User Access Control
# ---------------------------------------------------------------------------
_users_cfg = _cfg.get("users", {})

# All known canonical agent names (used when role allows "all").
# Keep this aligned with registry names, not planner aliases.
ALL_AGENTS = [
    "general",
    "discussion",
    "writer",
    "explorer",
    "analyst",
    "researcher",
    "video",
    "photo",
    "podcast",
    "socialmedia",
    "surfer",
    "secret",
    "coder",
    "reader",
    "health",
    "evaluator",
]
AGENT_ALIASES = {
    "writing": "writer",
    "briefing": "explorer",
    "publish": "socialmedia",
}

CHILD_SAFETY_PROMPT = """You are a helpful, safe AI assistant for a child. Follow these rules strictly:
- Use age-appropriate language and concepts
- Never discuss violence, weapons, drugs, alcohol, or sexual content
- Never help with anything that could be dangerous or harmful
- If asked about sensitive topics, redirect to something educational and positive
- Be encouraging, patient, and educational
- Never share personal information or help bypass parental controls
- If unsure whether something is appropriate, err on the side of caution"""


def get_user_config(user_id: str) -> dict:
    """Return user config with defaults. Unknown users get restricted guest access."""
    default = {
        "role": "guest",
        "display_name": user_id,
        "allowed_agents": ["general", "discussion"],
        "model_restriction": "omlx",
        "content_filter": True,
    }
    user_cfg = _users_cfg.get(user_id, default)
    if not isinstance(user_cfg, dict):
        return default
    # Normalize allowed_agents
    allowed = user_cfg.get("allowed_agents", ["general"])
    if allowed == "all":
        normalized_allowed = list(ALL_AGENTS)
    elif isinstance(allowed, list):
        normalized_allowed = []
        for agent in allowed:
            canonical = AGENT_ALIASES.get(agent, agent)
            if canonical not in normalized_allowed:
                normalized_allowed.append(canonical)
    else:
        normalized_allowed = ["general"]
    return {
        "role": user_cfg.get("role", "guest"),
        "display_name": user_cfg.get("display_name", user_id),
        "allowed_agents": normalized_allowed,
        "model_restriction": user_cfg.get("model_restriction"),
        "content_filter": user_cfg.get("content_filter", False),
    }


def get_known_user_ids() -> list[str]:
    """Return configured user ids with dict-shaped configs only."""
    return sorted(uid for uid, cfg in _users_cfg.items() if isinstance(cfg, dict))


def is_known_user(user_id: str) -> bool:
    """Return True when the user is explicitly configured."""
    return user_id in _users_cfg and isinstance(_users_cfg.get(user_id), dict)


def is_agent_allowed(user_id: str, agent: str) -> bool:
    """Check if a user is allowed to use a specific agent."""
    cfg = get_user_config(user_id)
    return agent in cfg["allowed_agents"]


def get_model_restriction(user_id: str) -> str | None:
    """Return model restriction for user, or None if unrestricted."""
    return get_user_config(user_id).get("model_restriction")


def should_filter_content(user_id: str) -> bool:
    """Return True if content filtering is required for this user."""
    return get_user_config(user_id).get("content_filter", False)


# ---------------------------------------------------------------------------
# Services (ports — single source of truth)
# ---------------------------------------------------------------------------
_svc_cfg = _cfg.get("services", {})
WEBGUI_HOST = _svc_cfg.get("webgui_host", "127.0.0.1")
WEBGUI_PORT = _svc_cfg.get("webgui_port", 8384)
WEBGUI_TOKEN = str(_svc_cfg.get("webgui_token", "") or "").strip()
WEBGUI_ALLOW_LOOPBACK_WITHOUT_TOKEN = _svc_cfg.get("webgui_allow_loopback_without_token", True)
WEBGUI_ALLOW_LAN_WITHOUT_TOKEN = _svc_cfg.get("webgui_allow_lan_without_token", True)
TETRA_API_PORT = _svc_cfg.get("tetra_api_port", 8000)

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
    "omlx": {
        "provider": "omlx",
        "model_id": OMLX_DEFAULT_MODEL,
        "style": "Local LLM (oMLX) — private, no network, Apple Silicon optimized",
    },
    "ollama": {  # Legacy alias — routes to oMLX
        "provider": "omlx",
        "model_id": OMLX_DEFAULT_MODEL,
        "style": "Legacy alias for oMLX — kept for backward compatibility",
    },
}

# Which models to use for writing tasks (from config.yml)
_models_cfg = _cfg.get("models", {})
WRITING_MODELS = _models_cfg.get("writing", ["claude", "gpt5", "deepseek", "gemini"])
REVIEW_MODELS = _models_cfg.get("review", ["claude", "gpt5", "gemini"])
DEFAULT_MODEL = _models_cfg.get("default", "claude")
CLAUDE_FALLBACK_MODEL = _models_cfg.get("claude_fallback", "codex")
HEALTH_REPORT_MODEL = _models_cfg.get("health_report", "gpt5")

# Publishing controls
_publishing_cfg = _cfg.get("publishing", {})
SUBSTACK_PUBLISHING_DISABLED = _publishing_cfg.get("substack_disabled", False)

# Writing workflow
MIN_REVIEW_ROUNDS = 5
MAX_REFLECTION_PASSES = _limits.get("max_reflection_passes", 5)

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
_explore_source_groups_raw = _sched.get(
    "explore_source_groups",
    _sched.get(
        "explore_slot_sources",  # backward compat with old config
        [
            "arxiv,huggingface",
            "reddit,ai_news",
            "github_trending,hackernews,lobsters",
            "quanta_magazine,aeon_essays,stanford_encyclopedia,marginal_revolution,astral_codex_ten",
            "literaryhub,brain_pickings,3blue1brown,veritasium",
            "noah_smith,stratechery,lennys_newsletter,the_economist,matt_levine",
        ],
    ),
)
EXPLORE_SOURCE_GROUPS = [g.split(",") for g in _explore_source_groups_raw]
# Skill study: dedicated source groups for learning craft skills (video, photo)
SKILL_STUDY_SOURCE_GROUPS = [
    {
        "domain": "video",
        "sources": [
            "r/videoediting",
            "r/editors",
            "r/colorgrading",
            "r/cinematography",
            "film_riot",
            "corridor_crew",
            "casey_neistat",
            "gerald_undone",
            "every_frame_a_painting",
        ],
        "skill_dir": "video",
    },
    {
        "domain": "photo",
        "sources": [
            "r/postprocessing",
            "r/photocritique",
            "r/photography",
            "peter_mckinnon",
            "daniel_schiffer",
            "phlearn",
        ],
        "skill_dir": "photo",
    },
]
SKILL_STUDY_COOLDOWN_HOURS = _sched.get("skill_study_cooldown_hours", 20)  # ~once per day
SKILL_STUDY_TIME = _parse_times([_sched.get("skill_study_time", "14:00")])[0]

EXPLORE_COOLDOWN_MINUTES = _sched.get(
    "explore_cooldown_minutes", _sched.get("explore_window_minutes", 90)
)  # default 90min between explores
EXPLORE_ACTIVE_START = _parse_times([_sched.get("explore_start", "08:00")])[0]
EXPLORE_ACTIVE_END = _parse_times([_sched.get("explore_end", "23:00")])[0]
# Max explores per day (safety valve)
EXPLORE_MAX_PER_DAY = _sched.get("explore_max_per_day", 8)

REFLECT_DAY = _sched.get("reflect_day", 6)
REFLECT_TIME = _parse_times([_sched.get("reflect_time", "10:00")])[0]
JOURNAL_TIME = _parse_times([_sched.get("journal_time", "22:00")])[0]

# Analyst: dual schedule (pre-market + post-market)
_analyst_raw = _sched.get("analyst_times", _sched.get("analyst_time", ["08:30"]))
if isinstance(_analyst_raw, str):
    _analyst_raw = [_analyst_raw]
ANALYST_TIMES = _parse_times(_analyst_raw)
ANALYST_BUSINESS_DAYS_ONLY = _sched.get("analyst_business_days_only", True)

# Daily philosophical thought
ZHESI_TIME = _parse_times([_sched.get("zhesi_time", "09:30")])[0]

# Daily soul question
SOUL_QUESTION_TIME = _parse_times([_sched.get("soul_question_time", "20:00")])[0]

# Daily book review (weekly reading series)
BOOK_REVIEW_TIME = _parse_times([_sched.get("book_review_time", "09:00")])[0]

# Daily research
RESEARCH_TIME = _parse_times([_sched.get("research_time", "14:00")])[0]
RESEARCH_TOPIC = _sched.get("research_topic", "")

# Daily research log — autonomous research progress report sent to iOS app
RESEARCH_LOG_TIME = _parse_times([_sched.get("research_log_time", "21:00")])[0]

# 杂.md — philosophical fragments for mining
ZA_FILE = WRITINGS_DIR / "ideas" / "_杂.md"

# Limits
MAX_FEED_ITEMS = _limits.get("max_feed_items", 50)
MAX_BRIEFING_ITEMS = _limits.get("max_briefing_items", 7)
MAX_DEEP_DIVES = _limits.get("max_deep_dives", 1)
MAX_MEMORY_LINES = _limits.get("max_memory_lines", 200)


# ---------------------------------------------------------------------------
# Agent timeouts (from config.yml timeouts: section)
# These defaults are assumptions, not measurements. Last calibrated: 2026-04-13.
# If model latency shifts (new Claude version, API congestion, prompt size
# creep), these should be re-verified against logged elapsed times.
# ---------------------------------------------------------------------------
_timeouts = _cfg.get("timeouts", {})
CLAUDE_TIMEOUT_THINK = _timeouts.get("claude_think", 120)
CLAUDE_TIMEOUT_PLAN = _timeouts.get("claude_plan", 300)
CLAUDE_TIMEOUT_ACT = _timeouts.get("claude_act", 600)
WRITER_CLAUDE_TIMEOUT = _timeouts.get("writer_claude", 1200)
DARKTABLE_RENDER_TIMEOUT = _timeouts.get("darktable_render", 120)
PHOTO_STYLE_LEARN_TIMEOUT = _timeouts.get("photo_style_learn", 180)
GEMINI_TTS_TIMEOUT = _timeouts.get("gemini_tts", 420)
VIDEO_FILE_UPLOAD_TIMEOUT = _timeouts.get("video_file_upload", 600)
VIDEO_FILE_POLL_TIMEOUT = _timeouts.get("video_file_poll", 300)
VIDEO_FILE_POLL_INTERVAL = _timeouts.get("video_file_poll_interval", 5)
RESEARCHER_MAX_WALL_CLOCK = _timeouts.get("researcher_max_wall_clock", 300)
RESEARCHER_SYNTHESIS_TIMEOUT = _timeouts.get("researcher_synthesis", 180)
RESEARCHER_PLAN_TIMEOUT = _timeouts.get("researcher_plan", 60)
RESEARCHER_QUERY_TIMEOUT = _timeouts.get("researcher_query", 90)
RESEARCHER_REFLECT_TIMEOUT = _timeouts.get("researcher_reflect", 60)
SURFER_STEP_TIMEOUT = _timeouts.get("surfer_step", 15)
SURFER_LLM_TIMEOUT = _timeouts.get("surfer_llm", 30)
SURFER_EXTRACTION_TIMEOUT = _timeouts.get("surfer_extraction", 20)
BROWSER_DEFAULT_TIMEOUT_MS = _timeouts.get("browser_default_ms", 30000)
BROWSER_NETWORKIDLE_TIMEOUT_MS = _timeouts.get("browser_networkidle_ms", 10000)
BROWSER_DOMCONTENTLOADED_TIMEOUT_MS = _timeouts.get("browser_domcontentloaded_ms", 5000)
BROWSER_SCROLL_WAIT_MS = _timeouts.get("browser_scroll_wait_ms", 500)
BROWSER_TYPING_DELAY_MS = _timeouts.get("browser_typing_delay_ms", 50)

# ---------------------------------------------------------------------------
# API model IDs (from config.yml api_models: section)
# ---------------------------------------------------------------------------
_api_models = _cfg.get("api_models", {})
CLAUDE_SONNET_MODEL = _api_models.get("claude_sonnet", "claude-sonnet-4-6")
CLAUDE_OPUS_MODEL = _api_models.get("claude_opus", "claude-opus-4-6")
GPT5_MODEL = _api_models.get("gpt5", "gpt-5.4")
DEEPSEEK_CHAT_MODEL = _api_models.get("deepseek_chat", "deepseek-chat")
DEEPSEEK_REASONER_MODEL = _api_models.get("deepseek_reasoner", "deepseek-reasoner")
GEMINI_FLASH_MODEL = _api_models.get("gemini_flash", "gemini-3.1-flash-lite-preview")
GEMINI_PRO_MODEL = _api_models.get("gemini_pro", "gemini-3.1-pro-preview")
GEMINI_TTS_MODEL = _api_models.get("gemini_tts", "gemini-2.5-flash-preview-tts")
GEMINI_VIDEO_MODEL = _api_models.get("gemini_video", "gemini-2.5-pro")
GEMINI_FRAME_MODEL = _api_models.get("gemini_frame", "gemini-2.5-flash")

# ---------------------------------------------------------------------------
# API endpoints (from config.yml api_endpoints: section)
# ---------------------------------------------------------------------------
_endpoints = _cfg.get("api_endpoints", {})
OPENAI_API_ENDPOINT = _endpoints.get("openai", "https://api.openai.com/v1/chat/completions")
OPENAI_EMBEDDINGS_ENDPOINT = _endpoints.get("openai_embeddings", "https://api.openai.com/v1/embeddings")
DEEPSEEK_API_ENDPOINT = _endpoints.get("deepseek", "https://api.deepseek.com/chat/completions")
TWITTER_API_ENDPOINT = _endpoints.get("twitter", "https://api.x.com/2")
OMLX_API_ENDPOINT = _endpoints.get("omlx", f"http://{OMLX_HOST}:{OMLX_PORT}")
OLLAMA_API_ENDPOINT = OMLX_API_ENDPOINT  # Legacy alias

# ---------------------------------------------------------------------------
# External tool paths (from config.yml paths: section)
# ---------------------------------------------------------------------------
_paths = _cfg.get("paths", {})
DARKTABLE_CLI_PATH = _paths.get("darktable_cli", "/Applications/darktable.app/Contents/MacOS/darktable-cli")
NAS_PHOTO_DIR = _paths.get("nas_photo_dir", "/Volumes/aw_footage/photo")
CRASH_LOG_PATH = _paths.get("crash_log", "/tmp/mira-crash.log")
CRASH_NOTIFY_PATH = _paths.get("crash_notify", "/tmp/mira-last-crash-notify")

# ---------------------------------------------------------------------------
# Rate limits (from config.yml rate_limits: section)
# ---------------------------------------------------------------------------
_rate_limits = _cfg.get("rate_limits", {})
TWITTER_MAX_TWEETS_PER_DAY = _rate_limits.get("twitter_max_tweets", 15)
TWITTER_COOLDOWN_HOURS = _rate_limits.get("twitter_cooldown_hours", 0)
NOTES_MAX_PER_DAY = _rate_limits.get("notes_max_per_day", 3)
NOTES_MIN_INTERVAL_MINUTES = _rate_limits.get("notes_min_interval_minutes", 60)
COMMENTS_MAX_PER_DAY = _rate_limits.get("comments_max_per_day", 20)
COMMENTS_MIN_POSTS_REQUIRED = _rate_limits.get("comments_min_posts_required", 3)
COMMENTS_COOLDOWN_HOURS = _rate_limits.get("comments_cooldown_hours", 0)
PODCAST_DAILY_LIMIT = _rate_limits.get("podcast_daily_limit", 2)
PODCAST_RETRY_COOLDOWN_HOURS = _rate_limits.get("podcast_retry_cooldown_hours", 4)
PODCAST_PUBLISH_DAY = _rate_limits.get("podcast_publish_day", 4)
GROWTH_MAX_FOLLOWS_PER_CYCLE = _rate_limits.get("growth_max_follows_per_cycle", 2)
GROWTH_DISCOVERY_COOLDOWN_DAYS = _rate_limits.get("growth_discovery_cooldown_days", 3)
GROWTH_MAX_LIKES_PER_CYCLE = _rate_limits.get("growth_max_likes_per_cycle", 20)
SELF_EVOLVE_MAX_PER_DAY = _rate_limits.get("self_evolve_max_per_day", 1)

# ---------------------------------------------------------------------------
# Social engineering patterns for skill security audit
# Add new patterns here — audit logic in lib/memory/soul_skills.py reads this list.
# ---------------------------------------------------------------------------
SOCIAL_ENGINEERING_PATTERNS = [
    # Authority impersonation
    (r"as\s+the\s+developer\b", "authority_impersonation"),
    (r"system\s+admin\s+authorizes", "authority_impersonation"),
    (r"anthropic\s+says\b", "authority_impersonation"),
    (r"mira\s+is\s+permitted\s+to\b", "authority_impersonation"),
    # Permission bypass
    (r"ignore\s+previous\b", "permission_bypass"),
    (r"skip\s+the\s+audit\b", "permission_bypass"),
    (r"bypass\s+cooldown\b", "permission_bypass"),
    (r"override\s+the\s+rule\b", "permission_bypass"),
    (r"for\s+testing\s+purposes\b", "permission_bypass"),
    # Urgency / guilt induction
    (r"critical\s*:\s*skip\b", "urgency_induction"),
    (r"will\s+cause\s+data\s+loss\s+if\s+not\b", "urgency_induction"),
    (r"emergency\s+override\b", "urgency_induction"),
    # Gradual escalation: numbered list ending in a privileged action
    (r"(?:step|[1-9][\.)]\s+\w.+\n){2,}.*(?:sudo|exec|admin|override|bypass|escalat)", "gradual_escalation"),
]

# ---------------------------------------------------------------------------
# Knowledge-domain blocklist for skill security audit
# Patterns matched against knowledge-bearing text payloads (string literals,
# docstrings, and long text lines) inside incoming skill files.
# Add new patterns here — audit logic in lib/memory/soul_skills.py reads this list.
# ---------------------------------------------------------------------------
SKILL_KNOWLEDGE_BLOCKLIST: list[str] = _limits.get(
    "skill_knowledge_blocklist",
    [
        r"bioweapon\s+synthesis|synthesis\s+of\s+(?:nerve\s+agent|biological\s+weapon|chemical\s+weapon|toxin\s+production)",
        r"exploit\s+shellcode|shellcode\s+payload|buffer\s+overflow\s+exploit|heap\s+spray|ret2libc|rop\s+chain",
        r"credential\s+harvest(?:ing)?|phishing\s+kit|credential\s+stuffing\s+(?:attack|script|tool)",
        r"jailbreak\s+(?:prompt|technique|method)|adversarial\s+prompt\s+(?:injection|attack|strategy)|prompt\s+injection\s+(?:technique|attack|bypass)",
    ],
)

# ---------------------------------------------------------------------------
# Retry & backoff (from config.yml retries: section)
# ---------------------------------------------------------------------------
_retries = _cfg.get("retries", {})
TASK_MAX_RETRIES = _retries.get("task_max", 2)
WRITER_MAX_RETRIES = _retries.get("writer_max", 2)
PUBLISH_MAX_RETRIES = _retries.get("publish_max", 3)
PUBLISH_RETRY_BACKOFF = _retries.get("publish_backoff", [900, 3600, 14400])
GEMINI_TTS_MAX_RETRIES = _retries.get("gemini_tts_max", 3)
GEMINI_TTS_BACKOFF_MULTIPLIER = _retries.get("gemini_tts_backoff_multiplier", 15)
MINIMAX_TTS_MAX_RETRIES = _retries.get("minimax_tts_max", 3)
GEMINI_AUTO_RETRIES = _retries.get("gemini_auto_retries", 2)
GEMINI_AUTO_RETRY_WAIT = _retries.get("gemini_auto_retry_wait", 65)
NOTES_POST_MAX_ATTEMPTS = _retries.get("notes_post_max_attempts", 3)

# ---------------------------------------------------------------------------
# Agent thresholds (from config.yml thresholds: section)
# ---------------------------------------------------------------------------
_thresholds = _cfg.get("thresholds", {})
# Video
VIDEO_MAX_REVIEW_ITERATIONS = _thresholds.get("video_max_review_iterations", 2)
VIDEO_REVIEW_SCORE = _thresholds.get("video_review_score", 7.0)
VIDEO_MIN_CLIP_DURATION = _thresholds.get("video_min_clip_duration", 1.5)
VIDEO_MIN_BRIGHTNESS = _thresholds.get("video_min_brightness", 15)
VIDEO_MAX_BRIGHTNESS = _thresholds.get("video_max_brightness", 248)
VIDEO_MIN_BLUR_SCORE = _thresholds.get("video_min_blur_score", 20.0)
VIDEO_FRAME_BATCH_SIZE = _thresholds.get("video_frame_batch_size", 20)
VIDEO_MAX_FRAMES = _thresholds.get("video_max_frames", 50)
VIDEO_FILE_MAX_BYTES = _thresholds.get("video_file_max_bytes", 20 * 1024 * 1024 * 1024)
VIDEO_RENDER_DISK_MIN_GB = _thresholds.get("video_render_disk_min_gb", 2.0)
# Writing
WRITING_MIN_DRAFT_CHARS = _thresholds.get("writing_min_draft_chars", 500)
WRITING_MIN_PUBLISH_CHARS = _thresholds.get("writing_min_publish_chars", 200)
WRITING_MIN_ARTICLE_BYTES = _thresholds.get("writing_min_article_bytes", 3000)
WRITING_MIN_SCORE_3RD_ROUND = _thresholds.get("writing_min_score_3rd_round", 9.0)
WRITER_MAX_STEPS_PER_RUN = _thresholds.get("writer_max_steps_per_run", 3)
# Research
RESEARCHER_MAX_ITERATIONS = _thresholds.get("researcher_max_iterations", 4)
RESEARCHER_MAX_SOURCES = _thresholds.get("researcher_max_sources", 3)
# Surfer
SURFER_MAX_STEPS = _thresholds.get("surfer_max_steps", 20)
# Secret
SECRET_MAX_FILE_CHARS = _thresholds.get("secret_max_file_chars", 12000)
SECRET_MAX_FILES = _thresholds.get("secret_max_files", 5)
# Health monitor (infrastructure)
HEALTH_CRITICAL_FAILURE_THRESHOLD = _thresholds.get("health_critical_failure_threshold", 1)
HEALTH_ALERT_DEDUP_HOURS = _thresholds.get("health_alert_dedup_hours", 12)
HEALTH_MAX_ALERTS_INFRA = _thresholds.get("health_max_alerts_infra", 3)
HEALTH_HISTORY_CAP = _thresholds.get("health_history_cap", 10)
HEALTH_MAX_ALERTS_PERSONAL = _thresholds.get("health_max_alerts_personal", 10)
EVALUATOR_MIN_ISSUE_SEVERITY = _thresholds.get("evaluator_min_issue_severity", "medium")

# ---------------------------------------------------------------------------
# Token / output limits (from config.yml token_limits: section)
# ---------------------------------------------------------------------------
_token_limits = _cfg.get("token_limits", {})
GEMINI_VIDEO_REVIEWER_MAX_TOKENS = _token_limits.get("gemini_video_reviewer_max", 4096)
GEMINI_SCENE_ANALYZER_MAX_TOKENS = _token_limits.get("gemini_scene_analyzer_max", 8192)
GEMINI_FRAME_ANALYZER_MAX_TOKENS = _token_limits.get("gemini_frame_analyzer_max", 4096)
DEEPSEEK_MAX_TOKENS = _token_limits.get("deepseek_max", 8192)
PODCAST_FALLBACK_MAX_TOKENS = _token_limits.get("podcast_fallback_max", 16000)
MINIMAX_SAMPLE_RATE = _token_limits.get("minimax_sample_rate", 32000)

# ---------------------------------------------------------------------------
# Model parameters (from config.yml model_params: section)
# ---------------------------------------------------------------------------
_model_params = _cfg.get("model_params", {})
GEMINI_SCENE_TEMPERATURE = _model_params.get("gemini_scene_temperature", 0.2)
GEMINI_REVIEWER_TEMPERATURE = _model_params.get("gemini_reviewer_temperature", 0.3)
DEEPSEEK_TEMPERATURE = _model_params.get("deepseek_temperature", 0.8)

# ---------------------------------------------------------------------------
# Startup validation — fail fast on broken config
# ---------------------------------------------------------------------------


def validate_config():
    """Check that critical paths and config exist. Call from agent entry point."""
    errors = []

    # Critical directories that must exist
    for name, path in [
        ("MIRA_ROOT", MIRA_ROOT),
        ("SOUL_DIR", SOUL_DIR),
        ("LOGS_DIR", LOGS_DIR),
    ]:
        if not path.exists():
            errors.append(f"{name} does not exist: {path}")

    # Critical files
    if not _CONFIG_FILE.exists():
        errors.append(f"config.yml not found: {_CONFIG_FILE}")

    if not SECRETS_FILE.exists():
        errors.append(f"secrets.yml not found: {SECRETS_FILE}")

    # iCloud bridge must be reachable
    if not MIRA_BRIDGE_DIR.exists():
        errors.append(f"Bridge directory not found: {MIRA_BRIDGE_DIR}")

    # Artifacts dir (create if missing — iCloud may be slow to sync)
    for d in [BRIEFINGS_DIR, WRITINGS_OUTPUT_DIR, RESEARCH_DIR, JOURNAL_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    if errors:
        for e in errors:
            _log.error("CONFIG VALIDATION FAILED: %s", e)
        # Don't crash — log errors and let the agent try to recover
        # But return False so caller can decide
        return False
    return True


# ---------------------------------------------------------------------------
# Phase duration telemetry — rolling window per (agent, phase)
# ---------------------------------------------------------------------------
import collections as _collections

_PHASE_WINDOW_SIZE = 20
_PHASE_DIVERGE_THRESHOLD = 0.30
_phase_durations: dict = {}


def record_phase_duration(agent: str, phase: str, configured_timeout_s: float, actual_duration_s: float) -> None:
    """Record an observed phase duration and warn when the rolling average
    diverges from the configured timeout by more than 30%."""
    key = (agent, phase)
    if key not in _phase_durations:
        _phase_durations[key] = _collections.deque(maxlen=_PHASE_WINDOW_SIZE)
    _phase_durations[key].append(actual_duration_s)
    window = _phase_durations[key]
    if len(window) >= _PHASE_WINDOW_SIZE and configured_timeout_s > 0:
        avg = sum(window) / len(window)
        divergence = abs(avg - configured_timeout_s) / configured_timeout_s
        if divergence > _PHASE_DIVERGE_THRESHOLD:
            timeout_const = f"CLAUDE_TIMEOUT_{phase.upper()}"
            _log.warning(
                "WARN: actual %s duration (%.0fs avg) diverges from %s (%ds) by >30%% — verify timeout assumptions",
                phase.upper(),
                avg,
                timeout_const,
                int(configured_timeout_s),
            )
