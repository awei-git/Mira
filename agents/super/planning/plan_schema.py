"""Plan step schema and agent alias normalization.

Single source of truth for what a valid plan step looks like and how
agent names are canonicalized.

Moved from task_worker.py to its own module for reuse by planner,
executor, and tests.
"""

from __future__ import annotations

import logging

log = logging.getLogger("mira.planning")

_VALID_TIERS = {"light", "heavy"}
_VALID_DIFFICULTIES = {"easy", "medium", "hard"}

# Alias map: names the planner might hallucinate → canonical registry names
AGENT_ALIASES = {
    "writing": "writer",
    "write": "writer",
    "briefing": "explorer",
    "explore": "explorer",
    "publish": "socialmedia",
    "publishing": "socialmedia",
    "substack": "socialmedia",
    "social": "socialmedia",
    "math": "researcher",
    "research": "researcher",
    "code": "coder",
    "coding": "coder",
    "browser": "surfer",
    "web": "surfer",
    "chat": "discussion",
    "talk": "discussion",
    "private": "secret",
    "privacy": "secret",
    "market": "analyst",
    "analysis": "analyst",
    "evaluate": "evaluator",
    "camera": "photo",
    "photography": "photo",
    "edit_video": "video",
    "film": "video",
    "medicine": "health",
    "medical": "health",
    "audio": "podcast",
    "tts": "podcast",
}


def normalize_agent_name(name: str, valid_agents: set) -> tuple[str | None, str | None]:
    """Normalize an agent name via alias map.

    Returns (canonical_name, alias_used) where alias_used is None if no
    normalization was needed, or the original name if it was aliased.
    """
    if name in valid_agents:
        return name, None
    canonical = AGENT_ALIASES.get(name)
    if canonical and canonical in valid_agents:
        return canonical, name
    return None, name


def validate_plan_step(step: dict, valid_agents: set) -> dict | None:
    """Validate a plan step against the required schema.

    Returns the validated (and normalized) step, or None if invalid.
    Schema:
      - agent: string, must be in valid_agents (aliases auto-resolved)
      - instruction: non-empty string
      - tier: 'light' or 'heavy' (defaults to 'light' if missing)
      - prediction: optional dict with difficulty/failure_modes/success_criteria
    """
    if not isinstance(step, dict):
        return None

    raw_agent = step.get("agent", "")
    agent, alias_used = normalize_agent_name(raw_agent, valid_agents)
    if alias_used:
        log.info("PLAN_ALIAS_NORMALIZE: '%s' → '%s'", alias_used, agent or "REJECTED")
    if agent is None:
        try:
            from ops.failure_log import record_failure

            record_failure(
                "planner",
                "validate_step",
                raw_agent,
                error_type="invalid_agent",
                error_message=f"Agent '{raw_agent}' not in valid set",
                context={"step": step, "valid_agents": sorted(valid_agents)},
            )
        except (ImportError, OSError):
            pass
        log.warning(
            "PLAN_STEP_REJECTED: agent='%s' not in valid set %s | step=%s", raw_agent, sorted(valid_agents), step
        )
        return None

    instruction = step.get("instruction", "").strip()
    if not instruction:
        return None

    tier = step.get("tier", "light")
    if tier not in _VALID_TIERS:
        tier = "light"

    validated = {"agent": agent, "instruction": instruction, "tier": tier}

    # Validate prediction block if present
    pred = step.get("prediction")
    if isinstance(pred, dict):
        difficulty = pred.get("difficulty", "medium")
        if difficulty not in _VALID_DIFFICULTIES:
            difficulty = "medium"
        failure_modes = pred.get("failure_modes", [])
        if not isinstance(failure_modes, list):
            failure_modes = []
        failure_modes = [str(m)[:100] for m in failure_modes[:5]]
        success_criteria = str(pred.get("success_criteria", ""))[:200]
        validated["prediction"] = {
            "difficulty": difficulty,
            "failure_modes": failure_modes,
            "success_criteria": success_criteria,
        }
    return validated
