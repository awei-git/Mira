"""Shared configuration compatibility module."""

import copy
import json
import os
from datetime import datetime, timedelta, timezone
from enum import Enum
from importlib import util as _importlib_util
from pathlib import Path as _Path

_LIB_CONFIG_PATH = _Path(__file__).resolve().parents[2] / "lib" / "config.py"
_spec = _importlib_util.spec_from_file_location("_mira_lib_config", _LIB_CONFIG_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load config from {_LIB_CONFIG_PATH}")

_lib_config = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(_lib_config)

_cfg = getattr(_lib_config, "_cfg", {})
_base_cfg = copy.deepcopy(_cfg) if isinstance(_cfg, dict) else {}
_timeouts_cfg = _cfg.get("timeouts", {}) if isinstance(_cfg, dict) else {}

for _name in dir(_lib_config):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_lib_config, _name)


DISCONTINUOUS_TRUST_ENABLED = False
REAUTH_ACTIONS = ["substack_publish", "file_delete_outside_sandbox", "external_api_write"]


def _config_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        raw = getattr(_lib_config, name, _cfg.get(name.lower(), default))
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw)


def _config_float_0_1(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        raw = getattr(_lib_config, name, _cfg.get(name.lower(), default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    return min(max(value, 0.0), 1.0)


ENABLE_CROSS_VERIFICATION: bool = _config_bool("ENABLE_CROSS_VERIFICATION", False)
ENABLE_PERMACOMPUTING_AUDIT: bool = _config_bool("ENABLE_PERMACOMPUTING_AUDIT", False)
# When true, permacomputing audit warnings block skill approval.
PERMACOMPUTING_STRICT = False
CROSS_VERIFY_IMPORTANCE_THRESHOLD: float = _config_float_0_1("CROSS_VERIFY_IMPORTANCE_THRESHOLD", 0.7)


class ConfigError(RuntimeError):
    pass


def _deep_merge_config(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_config(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _load_local_override(agent_name):
    global _timeouts_cfg
    if not isinstance(_cfg, dict):
        return {}

    name = str(agent_name or "").strip()
    if not name:
        return _cfg
    if name in {".", ".."} or "/" in name or "\\" in name or _Path(name).name != name:
        raise ConfigError(f"invalid agent name for local config: {agent_name!r}")

    local_path = _Path(__file__).resolve().parents[1] / name / "config.local.json"
    merged = copy.deepcopy(_base_cfg)
    if local_path.exists():
        try:
            local_cfg = json.loads(local_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"failed to load local config override for {name}: {exc}") from exc
        if not isinstance(local_cfg, dict):
            raise ConfigError(f"local config override for {name} must be a JSON object")
        merged = _deep_merge_config(merged, local_cfg)

    _cfg.clear()
    _cfg.update(merged)
    _timeouts_cfg = _cfg.get("timeouts", {}) if isinstance(_cfg, dict) else {}
    return _cfg


class FRICTION_TYPE(str, Enum):
    DELIBERATIVE = "deliberative"
    INFRASTRUCTURAL = "infrastructural"


FRICTION_LATENCY_THRESHOLDS_MS = {
    FRICTION_TYPE.DELIBERATIVE: int(os.getenv("FRICTION_DELIBERATIVE_LATENCY_THRESHOLD_MS", 10000)),
    FRICTION_TYPE.INFRASTRUCTURAL: int(os.getenv("FRICTION_INFRASTRUCTURAL_LATENCY_THRESHOLD_MS", 1000)),
}
FRICTION_CHECK_REGISTRY: list[dict] = [
    {
        "name": "content_looks_like_error",
        "pipeline": "publishing",
        "friction_type": FRICTION_TYPE.DELIBERATIVE,
        "description": "Blocks system errors, tracebacks, and pipeline residue from published content.",
    },
    {
        "name": "preflight_check",
        "pipeline": "dispatch,publishing",
        "friction_type": FRICTION_TYPE.DELIBERATIVE,
        "description": "Validates action payloads before side effects or artifact writes.",
    },
    {
        "name": "audit_skill",
        "pipeline": "dispatch",
        "friction_type": FRICTION_TYPE.DELIBERATIVE,
        "description": "Blocks unsafe generated or imported skills before save or enable.",
    },
    {
        "name": "anti_ai_checklist",
        "pipeline": "publishing",
        "friction_type": FRICTION_TYPE.DELIBERATIVE,
        "description": "Preserves public writing quality and voice before publication.",
    },
    {
        "name": "operational_audit.config_values",
        "pipeline": "dispatch",
        "friction_type": FRICTION_TYPE.INFRASTRUCTURAL,
        "description": "Runtime config spot-checks expose wiring drift and should be reduced with typed startup config.",
    },
    {
        "name": "operational_audit.soul_files",
        "pipeline": "dispatch",
        "friction_type": FRICTION_TYPE.DELIBERATIVE,
        "description": "Identity, memory, and journal availability preserve Mira continuity before dependent work runs.",
    },
    {
        "name": "operational_audit.notes_paths",
        "pipeline": "dispatch",
        "friction_type": FRICTION_TYPE.INFRASTRUCTURAL,
        "description": "Inbox/outbox existence checks are transport plumbing.",
    },
    {
        "name": "operational_audit.shared_imports",
        "pipeline": "dispatch",
        "friction_type": FRICTION_TYPE.INFRASTRUCTURAL,
        "description": "Import probes expose path/package fragility and should disappear behind stable module boundaries.",
    },
    {
        "name": "operational_audit.content_integrity",
        "pipeline": "dispatch,publishing",
        "friction_type": FRICTION_TYPE.DELIBERATIVE,
        "description": "Completed-task output checks catch hollow or truncated work before it is treated as done.",
    },
    {
        "name": "operational_audit.stuck_tasks",
        "pipeline": "dispatch",
        "friction_type": FRICTION_TYPE.INFRASTRUCTURAL,
        "description": "Stuck-task detection compensates for runtime state and polling limits.",
    },
    {
        "name": "operational_audit.network_connectivity",
        "pipeline": "dispatch",
        "friction_type": FRICTION_TYPE.INFRASTRUCTURAL,
        "description": "Network pings diagnose external reachability overhead.",
    },
    {
        "name": "operational_audit.survival_components",
        "pipeline": "dispatch",
        "friction_type": FRICTION_TYPE.DELIBERATIVE,
        "description": "Survival-critical component checks protect no-fallback safety and dispatch invariants.",
    },
]


def _normalize_friction_type(value) -> FRICTION_TYPE | None:
    if isinstance(value, FRICTION_TYPE):
        return value
    raw = str(value or "").strip().lower()
    for candidate in FRICTION_TYPE:
        if raw in {candidate.value, candidate.name.lower()}:
            return candidate
    return None


def register_friction_check(
    name: str,
    *,
    friction_type,
    latency_ms: float | int | None = None,
    threshold_ms: float | int | None = None,
    pipeline: str = "",
    description: str = "",
) -> dict:
    friction = _normalize_friction_type(friction_type)
    if friction is None:
        raise ValueError(f"invalid friction_type: {friction_type!r}")
    entry = {
        "name": str(name),
        "pipeline": str(pipeline or ""),
        "friction_type": friction,
        "description": str(description or ""),
    }
    if latency_ms is not None:
        entry["latency_ms"] = latency_ms
    if threshold_ms is not None:
        entry["threshold_ms"] = threshold_ms
    FRICTION_CHECK_REGISTRY.append(entry)
    return entry


def friction_audit(checks: list[dict] | None = None, thresholds_ms: dict | None = None) -> dict:
    """Collect registered checks and flag infrastructural latency over threshold."""
    source = list(FRICTION_CHECK_REGISTRY if checks is None else checks)
    thresholds = dict(FRICTION_LATENCY_THRESHOLDS_MS)
    for key, value in (thresholds_ms or {}).items():
        friction = _normalize_friction_type(key)
        if friction is not None:
            thresholds[friction] = value

    registered: list[dict] = []
    invalid: list[dict] = []
    exceeded: list[dict] = []
    for entry in source:
        if not isinstance(entry, dict):
            invalid.append({"entry": entry, "reason": "registry entry must be a dict"})
            continue
        friction = _normalize_friction_type(entry.get("friction_type"))
        if friction is None:
            invalid.append({"entry": entry, "reason": "missing or invalid friction_type"})
            continue
        item = dict(entry)
        item["friction_type"] = friction.value
        registered.append(item)

        if "latency_ms" not in entry:
            continue
        try:
            latency_ms = float(entry["latency_ms"])
            threshold_ms = float(entry.get("threshold_ms", thresholds[friction]))
        except (TypeError, ValueError):
            invalid.append({"entry": item, "reason": "latency_ms and threshold_ms must be numeric"})
            continue
        if friction == FRICTION_TYPE.INFRASTRUCTURAL and latency_ms > threshold_ms:
            exceeded.append(
                {
                    "name": item.get("name", ""),
                    "pipeline": item.get("pipeline", ""),
                    "friction_type": friction.value,
                    "latency_ms": latency_ms,
                    "threshold_ms": threshold_ms,
                }
            )

    return {
        "passed": not invalid and not exceeded,
        "registered": registered,
        "invalid": invalid,
        "infrastructural_latency_exceeded": exceeded,
    }


MIRA_ROOT = _lib_config.MIRA_ROOT


def _config_positive_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        publishing_cfg = _cfg.get("publishing", {}) if isinstance(_cfg, dict) else {}
        raw = getattr(_lib_config, name, publishing_cfg.get(name.lower(), default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


PUBLISH_SAFETY_LOCK_TTL_HOURS: float = _config_positive_float("PUBLISH_SAFETY_LOCK_TTL_HOURS", 6.0)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class _MiraConfigMeta(type):
    def __getattribute__(cls, name):
        if name == "publish_safety_locked":
            cls._refresh_safety_lock()
        return super().__getattribute__(name)

    def __setattr__(cls, name, value):
        if name == "publish_safety_locked":
            raise ConfigError("publish_safety_locked can only be changed with MiraConfig.set_safety_lock")
        super().__setattr__(name, value)


class MiraConfig(metaclass=_MiraConfigMeta):
    publish_safety_locked: bool = False
    publish_safety_lock_expires_at: str | None = None
    publish_safety_lock_reason: str = ""
    _allowed_writers = {"evaluator", "super"}
    _safety_lock_state_path = _Path(getattr(_lib_config, "STATE_DIR", MIRA_ROOT / "data" / "state")) / (
        "publish_safety_lock.json"
    )

    @classmethod
    def _read_safety_lock_state(cls) -> dict:
        try:
            data = json.loads(cls._safety_lock_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @classmethod
    def _write_safety_lock_state(cls, state: dict) -> None:
        cls._safety_lock_state_path.parent.mkdir(parents=True, exist_ok=True)
        cls._safety_lock_state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def _sync_safety_lock_attrs(cls, state: dict) -> None:
        type.__setattr__(cls, "publish_safety_locked", bool(state.get("locked", False)))
        type.__setattr__(cls, "publish_safety_lock_expires_at", str(state.get("expires_at") or ""))
        type.__setattr__(cls, "publish_safety_lock_reason", str(state.get("reason") or ""))

    @classmethod
    def _refresh_safety_lock(cls) -> None:
        state = cls._read_safety_lock_state()
        expires_at = _parse_utc_datetime(state.get("expires_at"))
        if state.get("locked") and expires_at is not None and expires_at <= _utc_now():
            state = {
                **state,
                "locked": False,
                "expired_at": _utc_now().isoformat(),
            }
            cls._write_safety_lock_state(state)
        cls._sync_safety_lock_attrs(state)

    @classmethod
    def set_safety_lock(cls, agent_name, value, reason: str = "") -> dict:
        writer = str(agent_name or "").strip().lower()
        if writer not in cls._allowed_writers:
            raise ConfigError(f"{writer or 'unknown'} is not authorized to write publish_safety_locked")

        locked = bool(value)
        now = _utc_now()
        expires_at = now + timedelta(hours=PUBLISH_SAFETY_LOCK_TTL_HOURS) if locked else None
        state = {
            "locked": locked,
            "updated_at": now.isoformat(),
            "updated_by": writer,
            "expires_at": expires_at.isoformat() if expires_at else "",
            "ttl_hours": PUBLISH_SAFETY_LOCK_TTL_HOURS,
            "reason": str(reason or ""),
        }
        cls._write_safety_lock_state(state)
        cls._sync_safety_lock_attrs(state)
        return state


CONTENT_GUARD_STRICTNESS = "medium"
CONTENT_DRIFT_ALERT_THRESHOLD = 0.3
DRIFT_CHECK_SAMPLE_RATE = 0.1
DRIFT_DIVERGENCE_THRESHOLD = 3
try:
    DRIFT_THRESHOLD: float = float(os.getenv("DRIFT_THRESHOLD", getattr(_lib_config, "DRIFT_THRESHOLD", 4.0)))
except (TypeError, ValueError):
    DRIFT_THRESHOLD = 4.0
AGENT_AUDIT_MODE = "off"
DELIBERATION_MODE: bool = True
HIGH_IMPACT_ACTION_PATTERNS: list = [
    "publish",
    "delete",
    "rm",
    "unlink",
    "skill.save",
    "skill.enable",
    "substack.post",
    "substack.note",
    "podcast.publish",
    "email.send",
]
DELIBERATION_LOG_PATH = "Mira/logs/deliberation.jsonl"
AUDIT_LOG_PATH: str = "logs/action_audit.jsonl"
AUDIT_LOG_ENABLED = True
AGENT_AUDIT_LOG = MIRA_ROOT / "logs" / "agent_audit.jsonl"
TOKEN_USAGE_LOG = MIRA_ROOT / "logs/token_usage.jsonl"
TOKEN_USAGE_LOG_PATH = TOKEN_USAGE_LOG
TOKEN_LOG_ENABLED = getattr(_lib_config, "TOKEN_LOG_ENABLED", True)
TIMING_LOG_ENABLED = True
TIMING_LOG_PATH = MIRA_ROOT / "logs" / "timing.jsonl"
LOCAL_MODEL_ENDPOINT_ALLOWLIST = list(
    getattr(_lib_config, "LOCAL_MODEL_ENDPOINT_ALLOWLIST", _cfg.get("local_model_endpoint_allowlist", []))
    or ["localhost", "127.0.0.1", "::1"]
)
MIRA_ALLOW_MODEL_NATIVE_TOOLS = False
MODEL_NATIVE_TOOL_DENYLIST = {"shell", "edit_file", "filesystem", "python", "exec"}
FORBIDDEN_CONTEXT_PATTERNS = [
    "思想动态",
    "student monitoring",
    "thought surveillance",
    "sentiment analysis on private",
    "monitor thoughts",
    "student sentiment scoring",
]
# Detects tool/agent boundary drift before unrequested side effects become invisible.
SCOPE_ESCALATION_MODE = "log_only"  # options: "log_only", "warn", "block"
SYNTHESIS_PASSTHROUGH_AGENTS = {"coder", "photo", "video", "researcher"}
EXTRACTION_FALLBACK_POLICY = getattr(_lib_config, "EXTRACTION_FALLBACK_POLICY", "deterministic_first")
HANDOFF_VERIFY_MIN_SIZE_BYTES = 50
HANDOFF_VERIFY_ERROR_PATTERNS = ["I cannot", "I am unable", "Error:", "Traceback", "failed to"]
CROSS_VALIDATION_ENABLED: bool = False
CROSS_VALIDATION_SAMPLE_RATE: float = 0.2
PEER_VERIFY_ENABLED: bool = False
PEER_VERIFY_THRESHOLD: str = "heavy"
PEER_VERIFY_TIMEOUT: int = 30
# Prevents middleware accumulation — if exceeded, trigger consolidation audit per cascading-config principle.
MAX_HARD_RULES = 7
# Swappable model identifier; agent code should not depend on a provider-specific literal.
LLM_MODEL = "claude-sonnet-4-20250514"
DEFAULT_MODEL = os.getenv("MIRA_MODEL", LLM_MODEL)
DEFAULT_MODEL_BACKEND = "claude"
EVAL_WRITER_MODEL = os.getenv("EVAL_WRITER_MODEL", LLM_MODEL)
TIER_MODEL_MAP = {
    "light": os.getenv("MODEL_LIGHT", LLM_MODEL),
    "heavy": os.getenv("MODEL_HEAVY", LLM_MODEL),
}
CLAUDE_TIMEOUT_THINK_HEAVY = int(
    os.getenv(
        "CLAUDE_TIMEOUT_THINK_HEAVY",
        getattr(_lib_config, "CLAUDE_TIMEOUT_THINK_HEAVY", _timeouts_cfg.get("claude_think_heavy", 300)),
    )
)
# Optional local fallback placeholder for future offline/resilience routing.
# LOCAL_FALLBACK_MODEL = None  # path to local .gguf or MLX model for offline/resilience (future use)
AGENT_REGISTRY = {
    "general": {
        "tier": "light",
        "permissions": {
            "network": "any",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "discussion": {
        "tier": "light",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "writer": {
        "tier": "heavy",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "explorer": {
        "tier": "light",
        "model_backend": "deepseek",
        "permissions": {
            "network": "any",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "analyst": {
        "tier": "heavy",
        "permissions": {
            "network": "any",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "researcher": {
        "tier": "heavy",
        "permissions": {
            "network": "any",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "video": {
        "tier": "light",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "photo": {
        "tier": "light",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "podcast": {
        "tier": "heavy",
        "permissions": {
            "network": "any",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "socialmedia": {
        "tier": "light",
        "permissions": {
            "network": "any",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "surfer": {
        "tier": "light",
        "permissions": {
            "network": "any",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "secret": {
        "tier": "light",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": True,
        },
    },
    "coder": {
        "tier": "light",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "reader": {
        "tier": "light",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "health": {
        "tier": "light",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": True,
        },
    },
    "evaluator": {
        "tier": "light",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "substack": {
        "tier": "heavy",
        "permissions": {
            "network": ["substack.com"],
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "super": {
        "tier": "light",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
}
AGENT_ACTION_SCOPE = {
    "general": ["file_write", "network_call"],
    "discussion": ["file_write"],
    "writer": ["file_write"],
    "explorer": ["file_write", "network_call"],
    "analyst": ["file_write", "network_call"],
    "researcher": ["file_write", "network_call"],
    "video": ["file_write"],
    "photo": ["file_write"],
    "podcast": ["file_write", "network_call"],
    "socialmedia": ["file_write", "network_call"],
    "surfer": ["file_write", "network_call"],
    "secret": ["file_write"],
    "coder": ["file_write", "install_package", "modify_config"],
    "reader": ["file_write"],
    "health": ["file_write"],
    "evaluator": ["file_write"],
    "substack": ["file_write", "network_call"],
    "super": ["file_write", "modify_config"],
    "task_worker": ["file_write", "network_call"],
}
QUALITY_CEILING = "high"  # AI can reach 'high' but not 'exceptional'; exceptional requires human obsession (see reading note 2026-05-10).
PUBLISH_OBSESSION_GATE_ENABLED = False
OBSESSION_GATE_TIMEOUT_HOURS = 24
SKILL_YIELD_FILE = MIRA_ROOT / "logs" / "skill_yield.json"
SKILL_EXTRACTION_DIGESTION_HOURS = 24  # Minimum hours between briefing generation and skill extraction; enforces 'friction as function' per reading note 2026-05-07.
MIN_DIFF_REVIEW_SECONDS = 30
LOG_RETENTION_DAYS = int(getattr(_lib_config, "LOG_RETENTION_DAYS", 30))
LAST_OUTPUT_FILE = getattr(_lib_config, "LAST_OUTPUT_FILE", MIRA_ROOT / "logs" / "last_output.json")
STALE_THRESHOLDS: dict[str, int] = dict(
    getattr(_lib_config, "STALE_THRESHOLDS", {"writer": 172800, "explorer": 21600, "reflect": 691200})
)
CALIBRATION_INTERVAL_DAYS = 7
CALIBRATION_SAMPLE_SIZE = 4
CODER_REQUIRE_HUMAN_REVIEW = True
_publishing_cfg = _cfg.get("publishing", {}) if isinstance(_cfg, dict) else {}
HIGH_STAKES_KEYWORDS = list(
    getattr(
        _lib_config,
        "HIGH_STAKES_KEYWORDS",
        _publishing_cfg.get(
            "high_stakes_keywords",
            [
                "diagnosis",
                "diagnoses",
                "treatment",
                "treatments",
                "medical advice",
                "health advice",
                "medication",
                "prescribe",
                "prescription",
                "therapy",
                "therapist",
                "legal",
                "legal advice",
                "lawyer",
                "attorney",
                "lawsuit",
                "sue",
                "court",
                "contract",
                "investment advice",
                "financial advice",
                "tax advice",
                "portfolio",
                "stocks",
                "securities",
                "retirement",
            ],
        ),
    )
)
try:
    PUBLISH_AUTO_CONFIDENCE_THRESHOLD = float(
        os.getenv(
            "PUBLISH_AUTO_CONFIDENCE_THRESHOLD",
            _publishing_cfg.get("auto_confidence_threshold", 0.8),
        )
    )
except (TypeError, ValueError):
    PUBLISH_AUTO_CONFIDENCE_THRESHOLD = 0.8
_coder_cfg = _cfg.get("coder", {}) if isinstance(_cfg, dict) else {}
CODER = {
    "skeptical_review": bool(_coder_cfg.get("skeptical_review", False)),
    "rationale": "Epistemic mode separation keeps code generation and adversarial audit in separate passes.",
}
CODER_SKEPTICAL_REVIEW = CODER["skeptical_review"]
# Human auditors lose track of logic beyond ~200 added lines per task (audit-capacity cliff).
MAX_AI_CODE_LINES_PER_TASK: int = int(os.getenv("MAX_AI_CODE_LINES_PER_TASK", 200))
AI_OUTPUT_REVIEW_BUDGET_LINES: int = int(os.getenv("AI_OUTPUT_REVIEW_BUDGET_LINES", 2000))
AI_CODE_BUDGET_LINES_PER_SESSION: int = int(os.getenv("AI_CODE_BUDGET_LINES_PER_SESSION", 300))
AI_CODE_BUDGET_RESET_HOURS: int = int(os.getenv("AI_CODE_BUDGET_RESET_HOURS", 24))
AI_OUTPUT_WARNING_RATIO: float = float(os.getenv("AI_OUTPUT_WARNING_RATIO", 0.7))
MAX_AUTO_EDITS_PER_FILE: int = int(os.getenv("MAX_AUTO_EDITS_PER_FILE", _coder_cfg.get("max_auto_edits_per_file", 5)))
MAX_CONSECUTIVE_AGENT_EDITS: int = int(
    os.getenv("MAX_CONSECUTIVE_AGENT_EDITS", _coder_cfg.get("max_consecutive_agent_edits", 5))
)
AGENT_EDIT_CHURN_THRESHOLD: int = int(
    os.getenv(
        "AGENT_EDIT_CHURN_THRESHOLD",
        _coder_cfg.get("agent_edit_churn_threshold", MAX_CONSECUTIVE_AGENT_EDITS),
    )
)
AGENT_EDIT_CHAIN_MAX = 5
AGENT_CODE_ENTROPY_ALERT_THRESHOLD: float = float(
    os.getenv("AGENT_CODE_ENTROPY_ALERT_THRESHOLD", _coder_cfg.get("agent_code_entropy_alert_threshold", 0.7))
)
AGENT_EDIT_CHURN_LOG = MIRA_ROOT / "logs" / "agent_edit_churn.json"
# Review-capacity guardrail for cumulative AI-generated code submitted to skill audit each day.
MAX_AUDIT_BYTES_PER_DAY: int = 50000
MAX_AUDITABLE_SKILL_LINES = 300  # Skills exceeding this require human review per cognitive-audit-boundary principle
MAX_CUMULATIVE_SKILL_LINES = 5000
MAX_LEARNED_SKILLS = 60
MAX_AGENT_EDITS_BEFORE_REVIEW = 5
SKILL_PROVENANCE_FILE = "data/skill_edit_provenance.json"
AI_REVIEW_DEBT_WARN_THRESHOLDS = [100, 500, 1000, 5000]
AI_REVIEW_DEBT_LOG_PATH = "logs/review_debt.json"


def _normalize_agent_edit_churn_filepath(filepath) -> str:
    raw = str(filepath or "").strip().strip("\"'")
    if not raw:
        return ""
    path = _Path(raw).expanduser()
    try:
        if path.is_absolute():
            return path.resolve().relative_to(MIRA_ROOT).as_posix()
    except (OSError, ValueError):
        pass
    raw = raw.replace("\\", "/").lstrip("./")
    if raw.startswith("Mira/"):
        raw = raw[len("Mira/") :]
    return raw


def reset_human_review(filepath) -> bool:
    key = _normalize_agent_edit_churn_filepath(filepath)
    if not key:
        return False
    try:
        data = json.loads(AGENT_EDIT_CHURN_LOG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    record = data.get(key)
    if not isinstance(record, dict):
        return False
    record["edits_since_last_human_review"] = 0
    record["flagged"] = False
    try:
        AGENT_EDIT_CHURN_LOG.parent.mkdir(parents=True, exist_ok=True)
        AGENT_EDIT_CHURN_LOG.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
    except OSError:
        return False
    return True


BLIND_SPOT_LOOKBACK_DAYS = 30
BLIND_SPOT_SILENCE_THRESHOLD_DAYS = 3
MAX_TASKS_PER_CYCLE = getattr(_lib_config, "MAX_TASKS_PER_CYCLE", 5)
MAX_UNDELIVERED_OUTPUTS = int(getattr(_lib_config, "MAX_UNDELIVERED_OUTPUTS", 5))
IPHONE_BRIDGE_WARN_LATENCY_MS = int(getattr(_lib_config, "IPHONE_BRIDGE_WARN_LATENCY_MS", 45000))
MAX_ATTRIBUTION_DEPTH = int(getattr(_lib_config, "MAX_ATTRIBUTION_DEPTH", 2))
MAX_SKILL_IMPORTS_PER_DAY = getattr(_lib_config, "MAX_SKILL_IMPORTS_PER_DAY", 20)
MAX_UNREVIEWED_SKILLS = int(getattr(_lib_config, "MAX_UNREVIEWED_SKILLS", 5))
MAX_SKILLS_PER_AGENT = getattr(_lib_config, "MAX_SKILLS_PER_AGENT", 12)
SKILL_EXAMPLE_ORDER: str = getattr(_lib_config, "SKILL_EXAMPLE_ORDER", "relevance_first")
EVALUATOR_MIN_ISSUE_SEVERITY = getattr(_lib_config, "EVALUATOR_MIN_ISSUE_SEVERITY", "medium")
DEEP_VERIFY_PROBABILITY = 0.15
DEEP_VERIFY_COOLDOWN_MINUTES = 120
ANTI_AI_FLOOR_THRESHOLD: float = float(os.getenv("ANTI_AI_FLOOR_THRESHOLD", 0.2))
SKILL_EFFICACY_WARNING = getattr(_lib_config, "SKILL_EFFICACY_WARNING", True)
SKILL_TRUST_TTL_DAYS = getattr(_lib_config, "SKILL_TRUST_TTL_DAYS", 7)
SKILL_AUDIT_TTL_DAYS = getattr(_lib_config, "SKILL_AUDIT_TTL_DAYS", SKILL_TRUST_TTL_DAYS)
SKILL_AUDIT_STRICT_MODE = getattr(_lib_config, "SKILL_AUDIT_STRICT_MODE", False)
_SKILL_AUDIT_OVERRIDE_MODES = {"strict", "log_only", "warn"}
SKILL_AUDIT_OVERRIDE_MODE = str(getattr(_lib_config, "SKILL_AUDIT_OVERRIDE_MODE", "strict")).strip().lower()
if SKILL_AUDIT_OVERRIDE_MODE not in _SKILL_AUDIT_OVERRIDE_MODES:
    SKILL_AUDIT_OVERRIDE_MODE = "strict"
SKILL_AUDIT_LOCKOUT_THRESHOLD = getattr(_lib_config, "SKILL_AUDIT_LOCKOUT_THRESHOLD", 5)
SKILL_AUDIT_LOCKOUT_WINDOW_MINUTES = getattr(_lib_config, "SKILL_AUDIT_LOCKOUT_WINDOW_MINUTES", 60)
SKILL_AUDIT_LOCKOUT_DURATION_MINUTES = getattr(_lib_config, "SKILL_AUDIT_LOCKOUT_DURATION_MINUTES", 30)
MIRA_SKILL_FATIGUE_THRESHOLD = getattr(_lib_config, "MIRA_SKILL_FATIGUE_THRESHOLD", 5)
WINDOW_MINUTES = getattr(_lib_config, "WINDOW_MINUTES", 60)
SOUL_DETERMINISTIC_AUDIT_ENABLED = getattr(_lib_config, "SOUL_DETERMINISTIC_AUDIT_ENABLED", True)
AUDIT_LAG_WARN_SECONDS = getattr(_lib_config, "AUDIT_LAG_WARN_SECONDS", 3600)
SKILL_NETWORK_WHITELIST = list(getattr(_lib_config, "SKILL_NETWORK_WHITELIST", []))
SKILL_KNOWLEDGE_BLOCKLIST = list(getattr(_lib_config, "SKILL_KNOWLEDGE_BLOCKLIST", []))
TRUSTED_SKILL_SOURCES = list(getattr(_lib_config, "TRUSTED_SKILL_SOURCES", []))
MAX_REFLECTION_PASSES = getattr(_lib_config, "MAX_REFLECTION_PASSES", 5)
RAW_WRITING_MODE_ALLOWED = True
WRITER_DE_AI_STRICTNESS = getattr(_lib_config, "WRITER_DE_AI_STRICTNESS", "strict")
SUSPENDED_METRICS: list[str] = list(
    getattr(_lib_config, "SUSPENDED_METRICS", ["reading_volume", "hallucination_rate", "emotional_range"])
)
DISABLED_RUBRICS: set[str] = set(
    getattr(
        _lib_config,
        "DISABLED_RUBRICS",
        {"reading_volume", "hallucination_rate", "emotional_range", "rubric_calibration"},
    )
)
MISCALIBRATION_FLAG_THRESHOLD: int = int(getattr(_lib_config, "MISCALIBRATION_FLAG_THRESHOLD", 3))
SCAFFOLDING_CATCH_RATE_WINDOW_HOURS: int = int(getattr(_lib_config, "SCAFFOLDING_CATCH_RATE_WINDOW_HOURS", 24))
WRITER_OBSESSION_MODE = False
ALLOW_VULNERABLE_VOICE = False
SURVIVAL_SKILL_SOURCES = []
# Components with no fallback; operational audit reports these separately.
SURVIVAL_CRITICAL_COMPONENTS = ["heartbeat", "notes_bridge", "task_worker", "preflight"]
SENSITIVE_SURVIVAL_TERMS = list(
    getattr(
        _lib_config,
        "SENSITIVE_SURVIVAL_TERMS",
        [
            # Survival-driven exposures must stay local.
            "妈妈去世",
            "怎么活下去",
            "自杀",
            "不想活了",
            "不想活",
            "不想活下去了",
            "失去亲人",
            "好想死",
            "想死",
            "我撑不住了",
            "撑不下去了",
            "活不下去了",
            "不想醒来",
            "结束生命",
            "去世后",
            "死了以后",
        ],
    )
)
SURVIVAL_EXPOSURE_PATTERNS = getattr(
    _lib_config,
    "SURVIVAL_EXPOSURE_PATTERNS",
    SENSITIVE_SURVIVAL_TERMS,
)
SENSITIVE_FORCE_LOCAL = getattr(_lib_config, "SENSITIVE_FORCE_LOCAL", True)
SENSITIVITY_HOURS_START = getattr(_lib_config, "SENSITIVITY_HOURS_START", 23)
SENSITIVITY_HOURS_END = getattr(_lib_config, "SENSITIVITY_HOURS_END", 6)
SENSITIVITY_ROUTE_TO_LOCAL = getattr(_lib_config, "SENSITIVITY_ROUTE_TO_LOCAL", True)
EXPLORE_MAX_PENDING_TASKS: int = int(getattr(_lib_config, "EXPLORE_MAX_PENDING_TASKS", 4))
EXPLORE_SOURCE_DIVERSITY_MIN_ENTITIES = 5
EXPLORE_SOURCE_ENTROPY_THRESHOLD = 0.6
EXPLORE_SOURCE_WINDOW = 16
EXPLORER_BRIEFING_FORMAT = os.getenv("EXPLORER_BRIEFING_FORMAT", "digest")
EXPLORER_PUBLIC_GROWTH_ENABLED = os.getenv("EXPLORER_PUBLIC_GROWTH_ENABLED", "false").lower() in {
    "1",
    "true",
    "yes",
}
EXPLORER_NARRATIVE_SOURCE_MIN_TYPES = 3
EXPLORER_CORPORATE_PR_MAX_RATIO = 0.4
FEED_SOURCE_TRUST = {
    "github_trending": 0.4,
    "arxiv": 1.0,
    "doi": 1.0,
    "official_changelog": 1.0,
    "official_changelogs": 1.0,
    "hacker_news": 0.6,
    "hackernews": 0.6,
    "reddit": 0.5,
}
SOURCE_TRUST_TIERS = {
    "arxiv": "primary",
    "doi": "primary",
    "official_changelog": "primary",
    "official_changelogs": "primary",
    "huggingface": "primary",
    "hacker_news": "community",
    "hackernews": "community",
    "reddit": "community",
    "lobsters": "community",
    "github_trending": "aggregator",
    "devto": "aggregator",
    "duckduckgo": "aggregator",
    "rss": "aggregator",
}
ENABLE_EPISTEMIC_FILTER = True
EPISTEMIC_CONFIDENCE_THRESHOLD = "medium"
PUBLISH_MAX_PER_WINDOW: int = 2
PUBLISH_WINDOW_MINUTES: int = 30
PUBLISH_COOLDOWN_PER_TYPE = {"article": 1440, "note": 120, "comment": 30, "tweet": 60}
SOCIAL_MAX_COMMENTS_PER_DAY: int = int(getattr(_lib_config, "SOCIAL_MAX_COMMENTS_PER_DAY", 5))
SOCIAL_MAX_NOTES_PER_DAY: int = int(getattr(_lib_config, "SOCIAL_MAX_NOTES_PER_DAY", 3))
EVAL_BENCHMARK_ROTATION_DAYS = 30
EVAL_WINDOW_DAYS = 7
EVAL_BENCHMARK_LAST_ROTATED: dict[str, str] = {}
SILENT_COMPLETION_MIN_RATIO = 0.3
SILENT_COMPLETION_HEDGE_PHRASES = [
    "unfortunately",
    "couldn't complete",
    "i don't know",
    "unable to",
    "failed to",
]
AUDIT_MODULE_HASH: str = "6da605108958f648dfcebde0c16cc5c26b80cec42807fbf98e8c2d55b3031887"  # pragma: allowlist secret
