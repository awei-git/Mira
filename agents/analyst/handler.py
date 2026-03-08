"""Analyst agent — answers market questions using Tetra data.

Tetra daily pipeline generates analysis + report + briefing artifact.
This handler loads the briefing as context and uses claude_think
to answer the user's specific question. No tool access needed.

Feedback loop: when the briefing can't answer a question, the question
is logged to tetra/feedback/gaps.jsonl. Tetra reads this on next run
to improve coverage.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from config import MIRA_DIR
from soul_manager import append_memory
from sub_agent import claude_think

log = logging.getLogger("analyst_agent")

_TETRA_DIR = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/MtJoy/Apps/tetra"
_BRIEFINGS_DIR = MIRA_DIR / "artifacts" / "briefings"
_REPORTS_DIR = _TETRA_DIR / "reports"
_FEEDBACK_FILE = _TETRA_DIR / "feedback" / "gaps.jsonl"


def _find_latest_briefing() -> Path | None:
    """Find the most recent Tetra briefing markdown."""
    if not _BRIEFINGS_DIR.exists():
        return None
    # Today first, then fall back to most recent
    today = datetime.now(tz=timezone.utc).date()
    target = _BRIEFINGS_DIR / f"{today.isoformat()}_market.md"
    if target.exists():
        return target
    briefings = sorted(_BRIEFINGS_DIR.glob("*_market.md"), reverse=True)
    return briefings[0] if briefings else None


def _find_latest_report() -> Path | None:
    """Find the most recent Tetra PDF report."""
    if not _REPORTS_DIR.exists():
        return None
    pdfs = sorted(_REPORTS_DIR.glob("tetra_*.pdf"), reverse=True)
    return pdfs[0] if pdfs else None


def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str,
           thread_history: str = "", thread_memory: str = "") -> str | None:
    """Answer a market question using Tetra briefing as context.

    1. Load latest Tetra briefing (real data from daily pipeline)
    2. claude_think answers the user's question with that context
    """
    log.info("Analyst task %s: answering with Tetra context", task_id)

    briefing_path = _find_latest_briefing()
    if not briefing_path:
        log.warning("No Tetra briefing found, cannot answer")
        return None

    briefing = briefing_path.read_text(encoding="utf-8").strip()
    if not briefing:
        log.warning("Tetra briefing is empty: %s", briefing_path.name)
        return None

    # Build context
    extra = ""
    if thread_history:
        extra += f"\n\n## Conversation History\n{thread_history}"
    if thread_memory:
        extra += f"\n\n## Thread Memory\n{thread_memory}"

    pdf = _find_latest_report()
    pdf_note = f"\n\nFull PDF report: `{pdf}`" if pdf else ""

    prompt = f"""Based on the following Tetra market intelligence, answer the user's question.
Use specific data from the report. Be concise and direct.

=== TETRA DAILY BRIEFING ({briefing_path.stem}) ===
{briefing}
{pdf_note}
{extra}

=== USER QUESTION ===
{content}

Answer in the same language as the question.
If the briefing doesn't cover this topic, start your answer with [GAP] and still try your best to answer.
"""

    result = claude_think(prompt, timeout=60)

    if result:
        # Detect gap — log feedback for Tetra to improve next run
        if result.startswith("[GAP]"):
            _log_gap(content, result)
            result = result.replace("[GAP]", "", 1).strip()

        (workspace / "output.md").write_text(result, encoding="utf-8")
        summary = result[:300]
        (workspace / "summary.txt").write_text(summary, encoding="utf-8")
        append_memory(f"Answered analyst question from {sender}: {content[:60]}")
        log.info("Analyst task %s: answered (%d chars)", task_id, len(result))
        return summary

    return None


def _log_gap(question: str, answer: str) -> None:
    """Log a coverage gap so Tetra can improve its briefing."""
    try:
        _FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "question": question[:200],
            "answer_preview": answer[:100],
        }
        with open(_FEEDBACK_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        log.info("Logged coverage gap: %s", question[:60])
    except Exception as e:
        log.warning("Failed to log gap: %s", e)
