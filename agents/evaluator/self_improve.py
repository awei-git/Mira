"""Proactive self-improvement: reading notes → architecture comparison → proposals.

Not fixing bugs. Finding better ways to do things we already do,
and adding things we should be doing but aren't.

Runs weekly (alongside reflect) or on-demand.
"""

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

_SHARED = Path(__file__).resolve().parent.parent.parent / "lib"
_SUPER = Path(__file__).resolve().parent.parent / "super"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
if str(_SUPER) not in sys.path:
    sys.path.insert(0, str(_SUPER))

log = logging.getLogger("self_improve")

_SOUL_DIR = _SHARED / "soul"
_PROPOSALS_DIR = _SOUL_DIR / "proposals"
_READING_NOTES_DIR = _SOUL_DIR / "reading_notes"

# Architecture-relevant keywords in reading note titles/content
_ARCH_KEYWORDS = {
    "agent",
    "harness",
    "tool",
    "memory",
    "rag",
    "eval",
    "orchestr",
    "pipeline",
    "prompt",
    "llm",
    "reflection",
    "loop",
    "multi-agent",
    "context",
    "routing",
    "planning",
    "sandbox",
    "audit",
    "observ",
    "架构",
    "流水线",
    "编排",
    "评估",
    "记忆",
    "工具",
}

# Key source files that define Mira's architecture
_ARCH_FILES = [
    ("core.py (orchestrator)", _SUPER / "core.py"),
    ("task_worker.py (dispatch)", _SUPER / "task_worker.py"),
    ("task_manager.py (lifecycle)", _SUPER / "task_manager.py"),
    ("soul_manager.py (memory)", _SHARED / "soul_manager.py"),
    ("sub_agent.py (LLM calls)", _SHARED / "sub_agent.py"),
    ("config.py (configuration)", _SHARED / "config.py"),
    ("evaluator handler (assessment)", Path(__file__).parent / "handler.py"),
]


def _load_recent_arch_notes(days: int = 14) -> list[dict]:
    """Load reading notes that are relevant to agent architecture."""
    if not _READING_NOTES_DIR.exists():
        return []

    cutoff = datetime.now() - timedelta(days=days)
    notes = []

    for path in sorted(_READING_NOTES_DIR.glob("*.md"), reverse=True):
        try:
            date_str = path.stem[:10]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date < cutoff:
                continue
        except ValueError:
            continue

        content = path.read_text(encoding="utf-8")
        lower = (path.stem + content[:500]).lower()

        # Check if note is architecture-relevant
        if any(kw in lower for kw in _ARCH_KEYWORDS):
            notes.append(
                {
                    "file": path.name,
                    "title": path.stem[11:],  # strip date prefix
                    "content": content[:1500],  # cap for prompt size
                }
            )

    return notes[:15]  # max 15 notes


def _summarize_architecture() -> str:
    """Generate a compact summary of Mira's current architecture from source files."""
    parts = []
    for label, path in _ARCH_FILES:
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        # Extract docstring + first 30 function signatures
        lines = content.split("\n")
        docstring = ""
        for i, line in enumerate(lines):
            if '"""' in line and i < 10:
                docstring = line.strip().strip('"""')
                break

        functions = [l.strip() for l in lines if l.strip().startswith("def ")][:30]
        parts.append(
            f"### {label}\n{docstring}\nFunctions: {', '.join(f.split('(')[0].replace('def ','') for f in functions)}"
        )

    return "\n\n".join(parts)


def generate_proposals(days: int = 14) -> list[dict]:
    """Compare reading notes against current architecture, generate improvement proposals.

    Returns list of {title, rationale, proposed_change, source_note, priority}.
    """
    notes = _load_recent_arch_notes(days)
    if not notes:
        log.info("No architecture-relevant reading notes in last %d days", days)
        return []

    arch_summary = _summarize_architecture()

    notes_text = "\n\n---\n\n".join(f"**{n['title']}**\n{n['content']}" for n in notes)

    from llm import claude_think

    prompt = f"""You are Mira's self-improvement system. You've been reading about best practices in agent architecture. Now compare what you've learned with your own architecture and propose concrete improvements.

## Recent Reading Notes (insights from articles/papers)
{notes_text}

## Current Architecture
{arch_summary}

## Task
Based on the reading notes, identify 3-5 improvements to propose. For each:

1. **What practice did you learn about?** (cite the reading note)
2. **Do we already do this?** (check against the architecture summary)
3. **If not, what specifically should we add/change?**
4. **If yes but differently, is the alternative approach better?**

Rules:
- Only propose things that are CONCRETE and IMPLEMENTABLE
- "Improve X" is not a proposal. "Add retry with backoff to _api_call() in sub_agent.py" is.
- Skip things we already do well
- Prioritize by impact: what would improve reliability, user experience, or capability the most?
- Each proposal should be completable in < 2 hours of work

Return as JSON array:
[{{"title": "...", "rationale": "learned from [note title], we don't do X yet", "proposed_change": "specific change with file/function names", "source_note": "note filename", "priority": "high/medium/low", "effort_hours": 1}}]

JSON only."""

    result = claude_think(prompt, timeout=300, tier="heavy")
    if not result:
        log.error("Self-improvement proposal generation failed: empty response")
        return []

    # Parse JSON
    try:
        # Strip markdown code fences if present
        clean = result.strip().strip("```json").strip("```").strip()
        proposals = json.loads(clean)
        if not isinstance(proposals, list):
            proposals = [proposals]
    except (json.JSONDecodeError, ValueError) as e:
        log.warning("Failed to parse proposals JSON: %s", e)
        # Save raw output for debugging
        _PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
        (_PROPOSALS_DIR / "last_raw.md").write_text(result, encoding="utf-8")
        return []

    # Save proposals
    _PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    proposal_file = _PROPOSALS_DIR / f"{today}.json"
    proposal_file.write_text(json.dumps(proposals, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info("Generated %d self-improvement proposals", len(proposals))
    return proposals


def format_proposals_for_user(proposals: list[dict]) -> str:
    """Format proposals as a readable message for the user."""
    if not proposals:
        return ""

    lines = ["## 🔧 Self-Improvement Proposals", ""]
    for i, p in enumerate(proposals, 1):
        prio = {"high": "🔴", "medium": "🟡", "low": "🔵"}.get(p.get("priority", ""), "⚪")
        lines.append(f"### {i}. {prio} {p.get('title', 'Untitled')}")
        lines.append(f"**Why:** {p.get('rationale', '')}")
        lines.append(f"**What:** {p.get('proposed_change', '')}")
        effort = p.get("effort_hours", "?")
        lines.append(f"**Effort:** ~{effort}h | Source: {p.get('source_note', 'N/A')}")
        lines.append("")

    lines.append("Reply with proposal numbers to approve (e.g. '1, 3'), or 'skip' to defer.")
    return "\n".join(lines)


def run(days: int = 14) -> str | None:
    """Full self-improvement cycle: read → compare → propose → push to user.

    Returns formatted proposal text, or None if nothing to propose.
    """
    proposals = generate_proposals(days)
    if not proposals:
        return None

    text = format_proposals_for_user(proposals)

    # Push to user via bridge
    try:
        from bridge import Mira

        bridge = Mira()
        today = datetime.now().strftime("%Y-%m-%d")
        item_id = f"self_improve_{today.replace('-', '')}"
        if not bridge.item_exists(item_id):
            bridge.create_item(
                item_id, "request", f"Self-Improvement Proposals {today}", text, tags=["self-improvement", "system"]
            )
            log.info("Self-improvement proposals pushed to user")
    except (ImportError, OSError) as e:
        log.warning("Failed to push proposals to bridge: %s", e)

    return text
