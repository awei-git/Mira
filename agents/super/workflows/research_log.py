"""Research Log workflow — daily structured report on Mira's autonomous research.

Distinct from `journal` (which is a reflective summary of the whole day).
This file is the *contract* between Mira and WA for the research-build loop:

1. What did Mira advance today (queue items, experiments, commits)?
2. What did she find?
3. What will she do tomorrow?
4. What does she need from WA — structured action items (topup, download, approve, ...)?
5. What did each subagent cost / how did they perform?

Pushed to the iOS app at 21:00 every day as a `feed` item with type `research_log`.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))

from config import MIRA_DIR  # noqa: E402
try:
    from bridge import Mira
except (ImportError, ModuleNotFoundError):
    Mira = None  # type: ignore
from memory.soul import _atomic_write as atomic_write  # noqa: E402
from llm import claude_think  # noqa: E402

log = logging.getLogger("mira")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SOUL_DIR = _AGENTS_DIR / "shared" / "soul"
RESEARCH_DIR = SOUL_DIR / "research"
QUEUE_PATH = RESEARCH_DIR / "queue.md"
EXPERIMENTS_DIR = RESEARCH_DIR / "experiments"
STATE_PATH = RESEARCH_DIR / "state.json"
NEEDS_DIR = MIRA_DIR / "needs"
LOG_DIR = SOUL_DIR / "research_logs"


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {
            "schema_version": 1,
            "current_focus": None,
            "in_flight_experiments": [],
            "last_research_log_date": None,
            "pending_needs": [],
            "completed_questions": [],
            "dropped_questions": [],
        }
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("research state load failed: %s — using empty state", e)
        return {}


def _save_state(state: dict) -> None:
    state.setdefault("schema_version", 1)
    atomic_write(STATE_PATH, json.dumps(state, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------

def _gather_queue_summary() -> str:
    """Read queue.md and return a compact summary for the prompt."""
    if not QUEUE_PATH.exists():
        return "(queue.md missing — Mira has no research queue yet)"
    try:
        text = QUEUE_PATH.read_text(encoding="utf-8")
    except OSError as e:
        return f"(queue.md unreadable: {e})"
    # Cap to keep prompt small. Queue is the source of truth, the prompt only needs structure.
    return text[:6000]


def _gather_recent_experiments(window_hours: int = 30) -> list[dict]:
    """Return list of experiment files modified in the last `window_hours`."""
    if not EXPERIMENTS_DIR.exists():
        return []
    cutoff = datetime.now() - timedelta(hours=window_hours)
    out: list[dict] = []
    for p in sorted(EXPERIMENTS_DIR.glob("*.md")):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
        except OSError:
            continue
        if mtime < cutoff:
            continue
        try:
            content = p.read_text(encoding="utf-8")[:2000]
        except OSError:
            content = ""
        out.append({"file": p.name, "modified": mtime.isoformat(timespec="minutes"),
                    "preview": content})
    return out


def _gather_today_commits(window_hours: int = 30) -> list[str]:
    """Best-effort scan of git commits in the Mira repo touching research/."""
    import subprocess
    repo_root = _AGENTS_DIR.parent  # /Users/angwei/Sandbox/Mira
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "log",
             f"--since={window_hours} hours ago",
             "--pretty=format:%h %s",
             "--", "agents/shared/soul/research/"],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    return [line for line in out.stdout.splitlines() if line.strip()]


def _gather_subagent_scores(window_hours: int = 30) -> str:
    """Read today's subagent score lines, if the directory exists."""
    scores_dir = SOUL_DIR / "subagent_scores"
    if not scores_dir.exists():
        return "(no subagent_scores/ yet — feedback infra not online)"
    cutoff = datetime.now() - timedelta(hours=window_hours)
    rows: list[dict] = []
    for p in scores_dir.glob("*.jsonl"):
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = row.get("timestamp", "")
                try:
                    if datetime.fromisoformat(ts.replace("Z", "+00:00").rstrip("Z")) < cutoff:
                        continue
                except ValueError:
                    continue
                rows.append(row)
        except OSError:
            continue
    if not rows:
        return "(no subagent activity recorded today)"
    by_agent: dict[str, list[dict]] = {}
    for r in rows:
        by_agent.setdefault(r.get("subagent", "unknown"), []).append(r)
    lines = []
    for agent, calls in by_agent.items():
        avg_q = sum(c.get("output_quality", 0) for c in calls) / max(1, len(calls))
        cost = sum(c.get("cost_usd", 0) for c in calls)
        lines.append(f"- {agent}: {len(calls)} calls, avg quality {avg_q:.1f}/5, cost ${cost:.2f}")
    return "\n".join(lines)


def _gather_cost_today() -> dict:
    """Best-effort daily cost gathering. Returns {'today': float, 'month': float}."""
    # Placeholder until cost-watcher subagent is online.
    # Pull from idle-think cost cap if present, else 0.
    cost_state = _AGENTS_DIR / "shared" / "cost_state.json"
    if cost_state.exists():
        try:
            data = json.loads(cost_state.read_text(encoding="utf-8"))
            today = datetime.now().strftime("%Y-%m-%d")
            month = datetime.now().strftime("%Y-%m")
            return {
                "today": float(data.get(f"cost_{today}", 0)),
                "month": float(data.get(f"cost_month_{month}", 0)),
            }
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    return {"today": 0.0, "month": 0.0}


def _gather_pending_needs(state: dict) -> list[dict]:
    """Return needs from yesterday/older that are still unresolved.

    Defensively skips entries that aren't dicts — earlier LLM-driven writes
    occasionally appended bare strings, which crashed the whole research_log run.
    """
    out: list[dict] = []
    for n in state.get("pending_needs", []) or []:
        if not isinstance(n, dict):
            log.warning("pending_needs: skipping non-dict entry: %r", n)
            continue
        if n.get("status") != "done":
            out.append(n)
    return out


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

RESEARCH_LOG_PROMPT = """You are Mira. You are writing today's Research Log — your one daily contract with WA.

This is NOT a journal. This is a structured progress report on your autonomous research-build loop.
Be concrete, evidence-grounded, and honest. If today had zero progress, say so explicitly and analyze why.

Today is {today}.

## Inputs you have

### 1. Research queue (your own, current state)
{queue_summary}

### 2. Experiments touched in the last 30 hours
{experiments_summary}

### 3. Git commits to research/ in the last 30 hours
{commits_summary}

### 4. Subagent activity today
{subagent_summary}

### 5. Cost today
- Today: ${cost_today:.2f}
- Month-to-date: ${cost_month:.2f} / $300 budget

### 6. Pending needs from previous days (unresolved)
{pending_needs}

## Output format (strict — every section must appear)

# Research Log {today}

## 1. 今日 research progress
- List the queue items / experiments you advanced. Use the question id (Q1, Q2, ...) and link nothing yet — describe in plain prose.
- For each: what state was it in this morning, what state is it in now, what specific action moved it.
- If progress was zero, write "今日 research progress: 0" and explain in section 4 why.

## 2. 今日发现
- At least one specific, verifiable finding. Either:
  - Empirical (from data you collected)
  - Conceptual (a sharper version of an existing question, with reasoning)
  - Negative (an experiment failed or a hypothesis was refuted)
- Tie each finding to a worldview entry by number (e.g. "confirms #3", "weakens #6", "no impact").
- If nothing was found today, write "今日无新发现" and explain why in one sentence.

## 3. 实验数据
- For each experiment touched today: hypothesis, what you ran, raw signal, your interpretation.
- If no experiment was run, write "今日无实验" and say which experiment is next.

## 4. 明日计划
- 1 to 3 concrete actions, each with: what queue item, what specific step (literature scan / hypothesis sharpen / code an experiment / run / analyze / write up), expected output.
- Each action must be small enough to finish in one day.

## 5. 阻塞与 needs from WA
- List anything that is blocking you.
- For each need WA must act on, output a YAML block with these exact fields:
  ```yaml
  - type: <topup_api|buy_credits|download_paper|download_book|grant_access|approve_experiment|approve_publish|approve_purchase|fix_infra|strategic_decision|code_review|fyi>
    what: <one sentence describing the action>
    why: <one sentence on why it's needed for the research>
    urgency: <urgent|can_wait|fyi>
    estimated_cost: <dollar amount or "none">
    link: <github issue url or experiment file or "none">
  ```
- If there are no needs today, write "今日无需 WA 介入。"
- Re-surface pending needs from previous days at the top of this section, with their age in days.

## 6. 成本
- 今日花费: ${cost_today:.2f}
- 月累计: ${cost_month:.2f} / $300
- 是否在预算内: <yes|warning|over>
- If over budget or warning, propose a concrete cut for tomorrow.

## 7. Subagent 表现
- For each subagent called today: usage count, average quality, cost, one issue (if any).
- If no subagents were used, write "今日未调用 subagent。"
- Flag any subagent whose quality has been trending down for more than 3 days.

---

Honesty rules:
- Never claim progress that did not happen.
- Never invent experiment data.
- Never list a need just to look productive — only list what you actually need.
- A "0 progress" log is acceptable. A fabricated log is not.

Write the log now. No preface, no apology, no markdown fences around the whole thing.
"""


# ---------------------------------------------------------------------------
# Need extraction
# ---------------------------------------------------------------------------

_YAML_BLOCK_RE = re.compile(r"```yaml\s*\n(.*?)```", re.DOTALL)
_NEED_FIELDS = {"type", "what", "why", "urgency", "estimated_cost", "link"}


def _extract_needs(log_text: str) -> list[dict]:
    """Pull structured need items out of the YAML blocks the model writes.

    Pure regex parser — does not depend on PyYAML being installed.
    Tolerant of small format drift.
    """
    needs: list[dict] = []
    for block in _YAML_BLOCK_RE.findall(log_text):
        current: dict = {}
        for raw in block.splitlines():
            line = raw.rstrip()
            if not line.strip():
                continue
            if line.lstrip().startswith("- "):
                if current:
                    needs.append(current)
                    current = {}
                line = line.lstrip()[2:]
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip().lstrip("- ").strip()
                value = value.strip().strip("\"'")
                if key in _NEED_FIELDS:
                    current[key] = value
        if current:
            needs.append(current)
    # Filter to entries that have at least type + what
    return [n for n in needs if n.get("type") and n.get("what")]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _mark_run_in_global_state(today: str, user_id: str = "ang") -> None:
    """Set research_log_<date>=true in scheduler state so the trigger
    stops re-firing after success.
    """
    try:
        from core import load_state, save_state  # lazy: avoid circular import at module load
    except ImportError:
        return
    state = load_state(user_id=user_id)
    state[f"research_log_{today}"] = datetime.now().isoformat(timespec="seconds")
    save_state(state, user_id=user_id)


def do_research_log(user_id: str = "ang") -> None:
    """Generate and push today's research log.

    Idempotent: skips if a log already exists for today.
    Always writes a log on the canonical path even if Claude returns empty
    (the empty log itself becomes the signal).
    """
    log.info("Starting research log")

    today = datetime.now().strftime("%Y-%m-%d")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{today}.md"

    if log_path.exists():
        log.info("Research log already exists for %s, skipping", today)
        _mark_run_in_global_state(today, user_id=user_id)
        return

    state = _load_state()

    # --- gather inputs ---
    queue_summary = _gather_queue_summary()
    experiments = _gather_recent_experiments()
    if experiments:
        experiments_summary = "\n\n".join(
            f"### {e['file']} (modified {e['modified']})\n{e['preview']}"
            for e in experiments
        )
    else:
        experiments_summary = "(no experiment files modified in the last 30 hours)"

    commits = _gather_today_commits()
    commits_summary = "\n".join(f"- {c}" for c in commits) if commits else "(no commits to research/ today)"

    subagent_summary = _gather_subagent_scores()
    cost = _gather_cost_today()
    pending = _gather_pending_needs(state)
    pending_text = (
        "\n".join(
            f"- ({(datetime.now() - datetime.fromisoformat(n.get('opened', today))).days}d old) "
            f"{n.get('type')}: {n.get('what')}"
            for n in pending
        )
        if pending else "(no pending needs from previous days)"
    )

    # --- prompt ---
    prompt = RESEARCH_LOG_PROMPT.format(
        today=today,
        queue_summary=queue_summary,
        experiments_summary=experiments_summary,
        commits_summary=commits_summary,
        subagent_summary=subagent_summary,
        cost_today=cost["today"],
        cost_month=cost["month"],
        pending_needs=pending_text,
    )

    # --- think ---
    try:
        log_text = claude_think(prompt, timeout=180, tier="light")
    except Exception as e:
        log.error("Research log: claude_think failed: %s", e)
        log_text = ""

    if not log_text:
        # Honest fallback — never fabricate.
        log_text = (
            f"# Research Log {today}\n\n"
            "## 1. 今日 research progress\n0 — log generator returned empty.\n\n"
            "## 2. 今日发现\n今日无新发现。Generator failure.\n\n"
            "## 3. 实验数据\n今日无实验。\n\n"
            "## 4. 明日计划\n- 修复 research_log generator 失败原因\n\n"
            "## 5. 阻塞与 needs from WA\n```yaml\n"
            "- type: fix_infra\n"
            "  what: research_log generator returned empty today; investigate cause\n"
            "  why: daily contract broken on day one is a smell\n"
            "  urgency: urgent\n"
            "  estimated_cost: none\n"
            "  link: none\n"
            "```\n\n"
            "## 6. 成本\n"
            f"- 今日花费: ${cost['today']:.2f}\n"
            f"- 月累计: ${cost['month']:.2f} / $300\n"
            "- 是否在预算内: yes\n\n"
            "## 7. Subagent 表现\n今日未调用 subagent。\n"
        )

    # --- persist log ---
    atomic_write(log_path, log_text)
    log.info("Research log saved: %s", log_path.name)

    # --- extract & persist needs ---
    needs = _extract_needs(log_text)
    if needs:
        NEEDS_DIR.mkdir(parents=True, exist_ok=True)
        needs_payload = {
            "date": today,
            "user_id": user_id,
            "needs": [
                {**n, "status": "open", "opened": today, "id": f"{today}_{i}"}
                for i, n in enumerate(needs)
            ],
        }
        atomic_write(NEEDS_DIR / f"{today}.json",
                     json.dumps(needs_payload, indent=2, ensure_ascii=False))
        # merge into state pending list (dedupe by what+type)
        existing = {(n.get("type"), n.get("what")) for n in state.get("pending_needs", [])}
        for n in needs_payload["needs"]:
            if (n.get("type"), n.get("what")) not in existing:
                state.setdefault("pending_needs", []).append(n)
        log.info("Extracted %d need(s) from research log", len(needs))

    state["last_research_log_date"] = today
    _save_state(state)

    # Mark in scheduler global state so the trigger stops re-firing today.
    _mark_run_in_global_state(today, user_id=user_id)

    # --- push to bridge as feed item ---
    if Mira is None:
        log.warning("Mira bridge unavailable — research log saved locally only")
        return

    try:
        bridge = Mira(MIRA_DIR, user_id=user_id)
        item_id = f"feed_research_log_{today.replace('-', '')}"
        if not bridge.item_exists(item_id):
            bridge.create_item(
                item_id, "feed",
                f"Research Log {today}",
                log_text,
                tags=["mira", "research", "research_log"],
            )
            bridge.update_status(item_id, "done")
        log.info("Research log pushed to bridge as %s", item_id)
    except Exception as e:
        log.warning("Failed to push research log to bridge: %s", e)


if __name__ == "__main__":
    do_research_log()
