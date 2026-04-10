#!/usr/bin/env python3
"""Mira Self-Evolution — daily reading-to-improvement pipeline.

Reads today's reading notes, filters for architecture/agent-relevant ones,
compares against Mira's own codebase, generates improvement proposals,
and auto-implements low-risk changes (max 1/day).

Usage:
    python self_evolve.py              # Full pipeline
    python self_evolve.py --dry-run    # Propose only, no auto-implement
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Paths
_HERE = Path(__file__).resolve().parent
_AGENTS_DIR = _HERE.parent
_MIRA_ROOT = _AGENTS_DIR.parent
_SHARED_DIR = _AGENTS_DIR .parent / "lib"
_PROPOSALS_DIR = _HERE / "proposals"
_READING_NOTES_DIR = _SHARED_DIR / "soul" / "reading_notes"
_CLAUDE_MD = _MIRA_ROOT.parent / "CLAUDE.md"

sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_SHARED_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [self-evolve] %(message)s")
log = logging.getLogger("self-evolve")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Keywords that signal a reading note is relevant to agent architecture
_RELEVANCE_KEYWORDS = [
    "agent", "harness", "pipeline", "memory", "architecture", "security",
    "tool", "self-improvement", "mutation", "evolution", "prompt",
    "framework", "trust", "permission", "supply chain", "attack",
    "eval", "benchmark", "scheduling", "dispatch", "concurrency",
    "context", "pollution", "contamination", "isolation",
    "mira", "config", "skill", "manifest", "audit",
    # Chinese equivalents
    "代理", "架构", "安全", "工具", "记忆", "管道", "调度",
    "权限", "信任", "攻击", "隔离", "自检", "进化",
]

_MAX_AUTO_IMPLEMENTS_PER_DAY = 1


# ---------------------------------------------------------------------------
# Step 1: Harvest today's reading notes
# ---------------------------------------------------------------------------

def harvest_reading_notes(date: str) -> list[dict]:
    """Read all reading notes for the given date. Returns list of {path, title, content}."""
    notes = []
    pattern = f"{date}_*.md"
    for note_path in sorted(_READING_NOTES_DIR.glob(pattern)):
        try:
            content = note_path.read_text(encoding="utf-8")
            # Extract title from first heading or filename
            title = note_path.stem.replace(f"{date}_", "").replace("-", " ")
            for line in content.splitlines():
                if line.startswith("# "):
                    title = line.lstrip("# ").strip()
                    break
            notes.append({
                "path": str(note_path),
                "filename": note_path.name,
                "title": title,
                "content": content,
            })
        except OSError as e:
            log.warning("Failed to read %s: %s", note_path, e)
    log.info("Harvested %d reading notes for %s", len(notes), date)
    return notes


def filter_relevant_notes(notes: list[dict]) -> list[dict]:
    """Filter notes for relevance to agent architecture, tools, security, memory."""
    relevant = []
    for note in notes:
        text = (note["title"] + " " + note["content"]).lower()
        matches = [kw for kw in _RELEVANCE_KEYWORDS if kw.lower() in text]
        if len(matches) >= 2:  # At least 2 keyword hits
            note["relevance_keywords"] = matches
            relevant.append(note)
    log.info("Filtered to %d relevant notes (from %d total)", len(relevant), len(notes))
    return relevant


# ---------------------------------------------------------------------------
# Step 2: Compare against Mira's architecture
# ---------------------------------------------------------------------------

def _load_architecture_context() -> str:
    """Build a concise summary of Mira's architecture for comparison."""
    sections = []

    # CLAUDE.md rules
    if _CLAUDE_MD.exists():
        try:
            text = _CLAUDE_MD.read_text(encoding="utf-8")
            # Extract just the hard rules and structure sections (first ~100 lines)
            lines = text.splitlines()[:100]
            sections.append("=== CLAUDE.md (rules + structure) ===\n" + "\n".join(lines))
        except OSError:
            pass

    # Agent registry — list of agents and capabilities
    try:
        from agent_registry import AgentRegistry
        registry = AgentRegistry()
        agents_summary = []
        for name in registry.list_agents():
            manifest = registry.get_manifest(name)
            if manifest:
                agents_summary.append(f"  - {name}: {manifest.description} (tier={manifest.tier})")
        sections.append("=== Agent Registry ===\n" + "\n".join(agents_summary))
    except Exception as e:
        log.warning("Could not load agent registry: %s", e)
        sections.append("=== Agent Registry === (failed to load)")

    # Core.py structure — task contracts and schedule
    core_path = _HERE / "core.py"
    if core_path.exists():
        try:
            core_text = core_path.read_text(encoding="utf-8")
            # Extract _DAILY_TASK_CONTRACTS block
            contracts_match = re.search(
                r'(_DAILY_TASK_CONTRACTS\s*=\s*\{.*?\n\})',
                core_text, re.DOTALL
            )
            if contracts_match:
                sections.append("=== Daily Task Contracts ===\n" + contracts_match.group(1)[:1500])

            # Extract should_* function names for schedule overview
            should_fns = re.findall(r'def (should_\w+)\(', core_text)
            sections.append("=== Schedule Functions ===\n" + "\n".join(f"  - {fn}()" for fn in should_fns))
        except OSError:
            pass

    # Config highlights
    try:
        from config import (
            MIRA_ROOT, CLAUDE_TIMEOUT_THINK, CLAUDE_TIMEOUT_ACT,
            MAX_CONCURRENT_TASKS, EXPLORE_COOLDOWN_MINUTES,
            EXPLORE_MAX_PER_DAY,
        )
        sections.append(f"""=== Key Config ===
  MIRA_ROOT: {MIRA_ROOT}
  CLAUDE_TIMEOUT_THINK: {CLAUDE_TIMEOUT_THINK}s
  CLAUDE_TIMEOUT_ACT: {CLAUDE_TIMEOUT_ACT}s
  MAX_CONCURRENT_TASKS: {MAX_CONCURRENT_TASKS}
  EXPLORE_COOLDOWN_MINUTES: {EXPLORE_COOLDOWN_MINUTES}
  EXPLORE_MAX_PER_DAY: {EXPLORE_MAX_PER_DAY}""")
    except Exception:
        pass

    return "\n\n".join(sections)


def compare_note_to_architecture(note: dict, arch_context: str) -> dict | None:
    """Use claude_think to compare a reading note against Mira's architecture.

    Returns a proposal dict or None if no improvement is identified.
    """
    from llm import claude_think

    prompt = f"""You are Mira's self-evolution module. Your job: read a reading note and compare it to Mira's own architecture to find a specific, concrete improvement.

## Reading Note
Title: {note['title']}
{note['content']}

## Mira's Architecture
{arch_context}

## Task
1. Does this reading note suggest a concrete improvement to Mira's codebase?
2. If yes, specify:
   - Which file(s) to change (exact paths under ~/Sandbox/Mira/)
   - What the change is (be specific: add a check, change a parameter, add a config option, etc.)
   - Why this improves Mira (cite the reading note's insight)
   - Risk level: "low" (config tweak, adding a check, parameter adjustment), "medium" (new function, changed logic), "high" (new agent, architecture change, changed data flow)
3. If no clear improvement: say "NO_IMPROVEMENT" and briefly explain why.

Respond in this exact JSON format (no markdown fences):
{{
  "has_improvement": true/false,
  "title": "short title for the proposal",
  "description": "what to change and why",
  "risk_level": "low|medium|high",
  "files_affected": ["path/to/file.py"],
  "rationale": "how the reading note insight maps to this improvement",
  "diff_description": "for low-risk: the exact change to make. for medium/high: detailed description of the change."
}}"""

    try:
        response = claude_think(prompt, timeout=120, tier="light")
        if not response or "NO_IMPROVEMENT" in response:
            return None

        # Extract JSON from response
        # Try to find JSON block
        json_match = re.search(r'\{[\s\S]*\}', response)
        if not json_match:
            log.warning("No JSON found in response for note: %s", note["title"])
            return None

        proposal = json.loads(json_match.group())
        if not proposal.get("has_improvement"):
            return None

        proposal["source_note"] = note["filename"]
        proposal["source_note_path"] = note["path"]
        return proposal

    except json.JSONDecodeError as e:
        log.warning("Failed to parse proposal JSON for %s: %s", note["title"], e)
        return None
    except Exception as e:
        log.warning("compare_note_to_architecture failed for %s: %s", note["title"], e)
        return None


# ---------------------------------------------------------------------------
# Step 3: Save proposals
# ---------------------------------------------------------------------------

def save_proposal(proposal: dict, date: str) -> Path:
    """Save a proposal as a JSON file. Returns the file path."""
    _PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)

    # Generate slug from title
    slug = re.sub(r'[^a-z0-9]+', '-', proposal.get("title", "untitled").lower())
    slug = slug.strip("-")[:50]

    filename = f"{date}_{slug}.json"
    filepath = _PROPOSALS_DIR / filename

    # Add metadata
    proposal["created_at"] = datetime.now().isoformat()
    proposal["date"] = date
    proposal["status"] = "proposed"  # proposal file status; backlog is source of execution truth

    filepath.write_text(
        json.dumps(proposal, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("Saved proposal: %s", filepath)
    _enqueue_backlog_action(proposal, filepath)
    return filepath


def _enqueue_backlog_action(proposal: dict, proposal_path: Path):
    """Mirror actionable proposals into the governed action backlog."""
    try:
        from ops.backlog import ActionBacklog, ActionItem

        risk = str(proposal.get("risk_level", "medium"))
        backlog = ActionBacklog()
        backlog.add(
            ActionItem(
                title=proposal.get("title", proposal_path.stem),
                description=proposal.get("description", "")[:500],
                source="self_evolve",
                status="approved" if risk == "low" else "proposed",
                priority="high" if risk == "low" else "medium",
                executor="self_evolve_proposal" if risk == "low" else "",
                payload={"proposal_path": str(proposal_path)},
            )
        )
    except Exception as exc:
        log.warning("Failed to enqueue proposal into backlog: %s", exc)


# ---------------------------------------------------------------------------
# Step 4: Auto-implement low-risk proposals
# ---------------------------------------------------------------------------

def auto_implement(proposal: dict, proposal_path: Path) -> dict:
    """Attempt to implement a low-risk proposal. Returns result dict.

    Strategy:
    1. Use claude_act to apply the change
    2. Run pytest
    3. If tests pass, keep. If fail, revert via git checkout.
    """
    from llm import claude_act

    files = proposal.get("files_affected", [])
    diff_desc = proposal.get("diff_description", "")

    if not files or not diff_desc:
        return {"success": False, "reason": "Missing files_affected or diff_description"}

    # Back up affected files
    backups = {}
    for f in files:
        fpath = Path(f).expanduser()
        if not fpath.is_absolute():
            fpath = _MIRA_ROOT / f
        if fpath.exists():
            try:
                backups[str(fpath)] = fpath.read_text(encoding="utf-8")
            except OSError:
                pass
        else:
            backups[str(fpath)] = None

    # Use claude_act to apply the change
    files_list = "\n".join(f"  - {f}" for f in files)
    prompt = f"""Apply this code change to the Mira codebase. Be precise — only change what's described.

## Change Description
{proposal.get('description', '')}

## Exact Change
{diff_desc}

## Files to Modify
{files_list}

## Rules
- Read each file first before editing
- Make minimal changes — only what's described above
- Do not add comments like "# Added by self-evolve" — keep the code clean
- Do not modify any other files
- After making changes, verify the files look correct by reading them back"""

    try:
        result = claude_act(prompt, cwd=_MIRA_ROOT, timeout=300, tier="light")
        if not result:
            _revert_files(backups)
            return {"success": False, "reason": "claude_act returned empty"}
    except Exception as e:
        _revert_files(backups)
        return {"success": False, "reason": f"claude_act failed: {e}"}

    changed = _changed_files(backups)
    if not changed:
        log.warning("Auto-implement produced no file changes for proposal %s", proposal_path.name)
        return {"success": False, "reason": "No file changes detected after implementation attempt"}

    # Run tests
    test_passed = _run_tests()
    if not test_passed:
        log.warning("Tests failed after auto-implement — reverting")
        _revert_files(backups)
        return {"success": False, "reason": "Tests failed after implementation"}

    # Update proposal status
    proposal["status"] = "implemented"
    proposal["implemented_at"] = datetime.now().isoformat()
    try:
        proposal_path.write_text(
            json.dumps(proposal, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass

    return {"success": True, "reason": "Implemented and tests passed"}


def _revert_files(backups: dict[str, str]):
    """Restore file contents from backup dict."""
    for path_str, content in backups.items():
        try:
            path = Path(path_str)
            if content is None:
                if path.exists():
                    path.unlink()
                    log.info("Removed newly created file: %s", path_str)
            else:
                path.write_text(content, encoding="utf-8")
                log.info("Reverted: %s", path_str)
        except OSError as e:
            log.error("Failed to revert %s: %s", path_str, e)


def _changed_files(backups: dict[str, str | None]) -> list[str]:
    """Return affected files whose content/existence changed."""
    changed = []
    for path_str, before in backups.items():
        path = Path(path_str)
        if before is None:
            if path.exists():
                changed.append(path_str)
            continue
        try:
            after = path.read_text(encoding="utf-8")
        except OSError:
            changed.append(path_str)
            continue
        if after != before:
            changed.append(path_str)
    return changed


def _run_tests() -> bool:
    """Run the test suite, return True if all pass."""
    test_file = _HERE / "tests" / "test_core.py"
    if not test_file.exists():
        log.warning("Test file not found: %s — assuming OK", test_file)
        return True
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-x", "-q"],
            capture_output=True, text=True, timeout=120,
            cwd=str(_MIRA_ROOT),
        )
        if result.returncode == 0:
            log.info("Tests passed")
            return True
        else:
            log.warning("Tests failed:\n%s", result.stdout[-500:] if result.stdout else result.stderr[-500:])
            return False
    except subprocess.TimeoutExpired:
        log.warning("Tests timed out")
        return False
    except Exception as e:
        log.warning("Test runner error: %s", e)
        return False


# ---------------------------------------------------------------------------
# Step 5: Report via Mira bridge
# ---------------------------------------------------------------------------

def send_report(proposals: list[dict], implementations: list[dict], date: str):
    """Send a feed item summarizing today's self-evolution results."""
    try:
        from config import MIRA_DIR
        from bridge import Mira

        bridge = Mira(MIRA_DIR)

        if not proposals and not implementations:
            # Don't spam if nothing happened
            log.info("No proposals or implementations — skipping report")
            return

        lines = [f"Self-Evolution Report {date}", ""]

        if proposals:
            lines.append(f"Analyzed {len(proposals)} reading note(s), generated proposals:")
            for p in proposals:
                risk = p.get("risk_level", "?")
                status = p.get("status", "proposed")
                lines.append(f"  [{risk}] {p.get('title', '?')} — {status}")
                lines.append(f"    Source: {p.get('source_note', '?')}")
            lines.append("")

        if implementations:
            lines.append("Auto-implemented:")
            for impl in implementations:
                success = "OK" if impl.get("success") else "FAILED"
                lines.append(f"  {success}: {impl.get('reason', '?')}")
            lines.append("")

        if not implementations and proposals:
            # All proposals were medium/high risk
            risk_counts = {}
            for p in proposals:
                r = p.get("risk_level", "unknown")
                risk_counts[r] = risk_counts.get(r, 0) + 1
            if "low" not in risk_counts:
                lines.append("No low-risk proposals today — all require manual review.")

        content = "\n".join(lines)

        bridge.create_item(
            item_id=f"self-evolve-{date.replace('-', '')}",
            item_type="feed",
            title=f"Self-Evolution: {date}",
            first_message=content,
            sender="agent",
            tags=["self-evolve", "system"],
            origin="agent",
        )
        log.info("Report sent to user via bridge")
    except Exception as e:
        log.error("Failed to send report: %s", e)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_evolve(dry_run: bool = False) -> dict:
    """Run the full self-evolution pipeline.

    Returns:
        dict with keys: date, notes_harvested, notes_relevant, proposals, implementations
    """
    log.info("=== Mira Self-Evolution ===")
    today = datetime.now().strftime("%Y-%m-%d")
    result = {
        "date": today,
        "notes_harvested": 0,
        "notes_relevant": 0,
        "proposals": [],
        "implementations": [],
    }

    # Step 1: Harvest
    notes = harvest_reading_notes(today)
    result["notes_harvested"] = len(notes)
    if not notes:
        log.info("No reading notes for today — nothing to evolve from")
        return result

    # Step 1b: Filter
    relevant = filter_relevant_notes(notes)
    result["notes_relevant"] = len(relevant)
    if not relevant:
        log.info("No architecture-relevant notes found — nothing to evolve from")
        return result

    # Step 2: Compare each note against architecture
    arch_context = _load_architecture_context()
    proposals = []

    for note in relevant:
        log.info("Comparing note: %s", note["title"][:60])
        proposal = compare_note_to_architecture(note, arch_context)
        if proposal:
            proposals.append(proposal)
            log.info("  -> Proposal: [%s] %s",
                     proposal.get("risk_level", "?"),
                     proposal.get("title", "?"))
        else:
            log.info("  -> No improvement identified")

    if not proposals:
        log.info("No proposals generated")
        send_report([], [], today)
        return result

    # Step 3: Save all proposals
    saved_paths = []
    for proposal in proposals:
        path = save_proposal(proposal, today)
        saved_paths.append(path)
    result["proposals"] = proposals

    # Step 4: Auto-implement low-risk (max 1 per day)
    implementations = []
    if not dry_run:
        low_risk = [(p, path) for p, path in zip(proposals, saved_paths)
                     if p.get("risk_level") == "low"]
        if low_risk:
            # Pick the first low-risk proposal
            proposal, path = low_risk[0]
            log.info("Auto-implementing low-risk: %s", proposal.get("title", "?"))
            impl_result = auto_implement(proposal, path)
            implementations.append(impl_result)
            log.info("Implementation result: %s", impl_result)
        else:
            log.info("No low-risk proposals — skipping auto-implementation")
    else:
        log.info("Dry run — skipping auto-implementation")

    result["implementations"] = implementations

    # Step 5: Report
    send_report(proposals, implementations, today)

    # Mark completion in agent state
    from core import load_state, save_state
    state = load_state()
    state[f"self_evolve_{today}"] = datetime.now().isoformat()
    state[f"self_evolve_{today}_actor"] = "self-evolve/claude-think"
    save_state(state)

    log.info("=== Self-Evolution complete: %d proposals, %d implementations ===",
             len(proposals), len(implementations))
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Mira Self-Evolution")
    parser.add_argument("--dry-run", action="store_true",
                        help="Propose only, do not auto-implement")
    args = parser.parse_args()
    run_evolve(dry_run=args.dry_run)
