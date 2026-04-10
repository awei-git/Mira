"""Research cycle — Mira's autonomous research-build loop.

Runs on a cooldown (default every 3 hours during waking hours) and advances
one research question by exactly one step. The step itself is decided by
Mira (Sonnet) based on the question's current state. Each cycle is bounded:

- One question per cycle.
- One step per cycle (literature scan / hypothesis sharpen / experiment design /
  experiment run / analyze / writeup).
- Cost ceiling per cycle = $0.50 (configurable per question via cost_ceiling).
- Always produces an artifact: either an updated experiment file or an
  appended note in the question section of queue.md.

This is the heart of the OPC pivot. Without this loop, research_log has
nothing to report.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))

from memory.soul import _atomic_write as atomic_write  # noqa: E402
from llm import claude_think  # noqa: E402

log = logging.getLogger("mira")

SOUL_DIR = _AGENTS_DIR / "shared" / "soul"
RESEARCH_DIR = SOUL_DIR / "research"
QUEUE_PATH = RESEARCH_DIR / "queue.md"
EXPERIMENTS_DIR = RESEARCH_DIR / "experiments"
STATE_PATH = RESEARCH_DIR / "state.json"
WORLDVIEW_PATH = SOUL_DIR / "worldview.md"

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("research state load failed: %s", e)
        return {}


def _save_state(state: dict) -> None:
    state.setdefault("schema_version", 1)
    atomic_write(STATE_PATH, json.dumps(state, indent=2, ensure_ascii=False))


def _mark_dispatched_in_global_state(user_id: str = "ang") -> None:
    """Set last_research_cycle in the scheduler's global state file so the
    cooldown trigger sees this run. Mirrors how social.do_growth_cycle handles it.
    """
    try:
        from core import load_state, save_state  # lazy: avoid circular import at module load
    except ImportError:
        return
    state = load_state(user_id=user_id)
    state["last_research_cycle"] = datetime.now().isoformat(timespec="seconds")
    save_state(state, user_id=user_id)


# ---------------------------------------------------------------------------
# Queue parsing
# ---------------------------------------------------------------------------

# A queue item is a section starting with "## Q<n> — <title>" until the next
# "## " or end-of-file.
_Q_HEADING_RE = re.compile(r"^##\s+(Q\d+)\s+[—\-–]\s+(.+)$", re.MULTILINE)
_STATUS_RE = re.compile(r"^\s*-\s*\*\*Status:\*\*\s*([a-z_]+)", re.MULTILINE)
_PRIORITY_RE = re.compile(r"^\s*-\s*\*\*Priority:\*\*\s*(P\d)", re.MULTILINE)


def _parse_queue() -> list[dict]:
    """Return [{id, title, status, priority, body, span}, ...] sorted by priority."""
    if not QUEUE_PATH.exists():
        return []
    text = QUEUE_PATH.read_text(encoding="utf-8")
    items: list[dict] = []
    matches = list(_Q_HEADING_RE.finditer(text))
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end]
        status_match = _STATUS_RE.search(body)
        priority_match = _PRIORITY_RE.search(body)
        items.append({
            "id": m.group(1),
            "title": m.group(2).strip(),
            "status": status_match.group(1) if status_match else "unknown",
            "priority": priority_match.group(1) if priority_match else "P9",
            "body": body,
            "span": (start, end),
        })
    return items


def _pick_next_question(items: list[dict]) -> dict | None:
    """Highest-priority item in `in_progress` first, then `next`."""
    actionable = [i for i in items if i["status"] in ("in_progress", "next")]
    if not actionable:
        return None
    # in_progress first, then by priority (P0 lowest number = highest priority)
    actionable.sort(key=lambda i: (
        0 if i["status"] == "in_progress" else 1,
        i["priority"],
    ))
    return actionable[0]


# ---------------------------------------------------------------------------
# Step decision prompt
# ---------------------------------------------------------------------------

STEP_DECISION_PROMPT = """You are Mira, advancing your own research queue.

You will pick exactly one next step for question {qid} and execute it now.
Allowed step types (pick one):

1. literature_scan — survey existing work, list 3-5 sources, extract relevant findings
2. hypothesis_sharpen — restate the hypothesis more precisely; identify what would falsify it
3. experiment_design — write the experimental protocol (method, setup, controls, metrics, sample size, expected runtime, expected cost)
4. experiment_run — execute the experiment as designed (write Python code that can run, or describe the manual procedure if code is not feasible at this step)
5. experiment_analyze — interpret results from a completed run
6. writeup — draft a short report (300-1000 words) summarizing finding + worldview impact

Pick the smallest step that moves the question forward. If the question is in `next` status, the right first step is usually literature_scan or hypothesis_sharpen (rarely experiment_design directly).

## Question
{question_body}

## Worldview entries this question touches (for context)
{worldview_excerpt}

## Output format

Return EXACTLY this JSON (no prose before or after, no markdown fence):

{{
  "step_type": "<one of the 6 above>",
  "reasoning": "<one sentence on why this step is right now>",
  "artifact": "<the actual content of this step — markdown, code, or prose. This is what gets saved.>",
  "new_status": "<in_progress | parked | done>",
  "next_step_hint": "<one sentence describing what the next cycle should do for this question>",
  "estimated_cost_usd": <float, the cost of THIS cycle's call, your honest estimate>,
  "worldview_delta": "<one sentence: confirm/refine/refute which worldview entry, or 'no_delta'>"
}}

Constraints:
- The artifact must be substantive. No "TODO" or "to be done later".
- For experiment_run: if you can't run the experiment in this single cycle (e.g. needs API calls beyond cycle budget), pick experiment_design instead and produce a runnable spec.
- For literature_scan: name actual sources you know exist (papers, blog posts, repos). Do not fabricate URLs.
- For hypothesis_sharpen: produce the new hypothesis statement, not commentary about it.
"""


def _load_worldview_excerpt() -> str:
    """Just enough worldview context for the step decision."""
    if not WORLDVIEW_PATH.exists():
        return "(worldview file missing)"
    try:
        return WORLDVIEW_PATH.read_text(encoding="utf-8")[:4000]
    except OSError:
        return "(worldview unreadable)"


# ---------------------------------------------------------------------------
# Artifact persistence
# ---------------------------------------------------------------------------

def _ensure_experiment_file(qid: str) -> Path:
    EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPERIMENTS_DIR / f"{qid}.md"
    if not path.exists():
        atomic_write(path, f"# {qid} Experiment Log\n\n")
    return path


def _append_experiment_step(qid: str, step_type: str, reasoning: str, artifact: str,
                             cost_usd: float, worldview_delta: str) -> Path:
    path = _ensure_experiment_file(qid)
    existing = path.read_text(encoding="utf-8")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    block = (
        f"\n## {timestamp} — {step_type}\n\n"
        f"**Reasoning:** {reasoning}\n\n"
        f"**Cost:** ${cost_usd:.3f}\n\n"
        f"**Worldview delta:** {worldview_delta}\n\n"
        f"---\n\n"
        f"{artifact}\n\n"
    )
    atomic_write(path, existing + block)
    return path


# ---------------------------------------------------------------------------
# Queue update
# ---------------------------------------------------------------------------

def _update_queue_status(qid: str, new_status: str, next_step_hint: str) -> None:
    """Rewrite the Status line of a question; append a Next-step hint."""
    if not QUEUE_PATH.exists():
        return
    text = QUEUE_PATH.read_text(encoding="utf-8")
    items = _parse_queue()
    target = next((i for i in items if i["id"] == qid), None)
    if not target:
        log.warning("research_cycle: qid %s not found in queue.md", qid)
        return

    body = target["body"]

    # Replace status line
    new_body = re.sub(
        r"^(\s*-\s*\*\*Status:\*\*)\s*[a-z_]+",
        rf"\1 {new_status}",
        body,
        count=1,
        flags=re.MULTILINE,
    )

    # Replace or insert "Last cycle" line right after the Status line
    last_line = f"- **Last cycle:** {datetime.now().strftime('%Y-%m-%d %H:%M')} — {next_step_hint}"
    if "**Last cycle:**" in new_body:
        new_body = re.sub(
            r"^\s*-\s*\*\*Last cycle:\*\*.*$",
            last_line,
            new_body,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        # Insert immediately after the status line
        new_body = re.sub(
            r"^(\s*-\s*\*\*Status:\*\*\s*[a-z_]+)\s*$",
            rf"\1\n{last_line}",
            new_body,
            count=1,
            flags=re.MULTILINE,
        )

    # Make sure the section ends with a single trailing blank line so the next
    # `## Q...` heading is preserved cleanly.
    new_body = new_body.rstrip() + "\n\n"

    start, end = target["span"]
    new_text = text[:start] + new_body + text[end:]
    atomic_write(QUEUE_PATH, new_text)


# ---------------------------------------------------------------------------
# Robust JSON extraction
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict | None:
    """Pull the first balanced JSON object out of model output. Tolerant."""
    if not text:
        return None
    # Strip code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    # Find first { and balance braces
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def do_research_cycle(user_id: str = "ang") -> dict:
    """Advance one research question by one step.

    Returns a small dict describing what happened (used by research_log).
    """
    log.info("Starting research cycle")
    # Mark dispatch in scheduler state immediately so the cooldown holds even if
    # this cycle errors out partway. We do not want a failed cycle to retry
    # every dispatch loop.
    _mark_dispatched_in_global_state(user_id=user_id)

    items = _parse_queue()
    if not items:
        log.warning("research_cycle: queue.md has no questions")
        return {"status": "empty_queue"}

    target = _pick_next_question(items)
    if not target:
        log.info("research_cycle: no actionable items (all parked/done/dropped)")
        return {"status": "no_actionable_items"}

    log.info("research_cycle: advancing %s (%s, status=%s)",
             target["id"], target["title"], target["status"])

    prompt = STEP_DECISION_PROMPT.format(
        qid=target["id"],
        question_body=target["body"],
        worldview_excerpt=_load_worldview_excerpt(),
    )

    try:
        response = claude_think(prompt, timeout=240, tier="light")
    except Exception as e:
        log.error("research_cycle: claude_think failed: %s", e)
        return {"status": "claude_error", "error": str(e), "qid": target["id"]}

    if not response:
        log.error("research_cycle: empty response from claude_think")
        return {"status": "empty_response", "qid": target["id"]}

    decision = _extract_json(response)
    if not decision or "step_type" not in decision or "artifact" not in decision:
        log.error("research_cycle: could not parse decision JSON; preview: %s",
                  response[:300])
        return {"status": "parse_error", "qid": target["id"], "preview": response[:300]}

    # Persist artifact
    artifact_path = _append_experiment_step(
        qid=target["id"],
        step_type=decision.get("step_type", "unknown"),
        reasoning=decision.get("reasoning", ""),
        artifact=decision.get("artifact", ""),
        cost_usd=float(decision.get("estimated_cost_usd", 0.0)),
        worldview_delta=decision.get("worldview_delta", "no_delta"),
    )

    # Update queue.md
    new_status = decision.get("new_status", "in_progress")
    if new_status not in ("in_progress", "parked", "done"):
        new_status = "in_progress"
    _update_queue_status(
        qid=target["id"],
        new_status=new_status,
        next_step_hint=decision.get("next_step_hint", ""),
    )

    # Update state.json
    state = _load_state()
    state["current_focus"] = target["id"]
    state["last_cycle"] = {
        "qid": target["id"],
        "step_type": decision.get("step_type"),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "new_status": new_status,
        "cost_usd": float(decision.get("estimated_cost_usd", 0.0)),
        "worldview_delta": decision.get("worldview_delta", "no_delta"),
    }
    if new_status == "done":
        completed = state.setdefault("completed_questions", [])
        if target["id"] not in completed:
            completed.append(target["id"])
    _save_state(state)

    log.info("research_cycle: %s advanced via %s -> status=%s",
             target["id"], decision.get("step_type"), new_status)

    return {
        "status": "advanced",
        "qid": target["id"],
        "step_type": decision.get("step_type"),
        "new_status": new_status,
        "artifact_path": str(artifact_path),
        "cost_usd": float(decision.get("estimated_cost_usd", 0.0)),
        "worldview_delta": decision.get("worldview_delta", "no_delta"),
    }


if __name__ == "__main__":
    result = do_research_cycle()
    print(json.dumps(result, indent=2, ensure_ascii=False))
