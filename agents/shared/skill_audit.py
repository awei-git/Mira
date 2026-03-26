"""Skill audit — find and fix the worst skills in the Mira system.

Reads all learned skills, evaluates each on trigger clarity, actionability,
and information density, then ranks them by quality gap x usage frequency.
Outputs a prioritized fix list.

Usage:
    from skill_audit import audit_all_skills, optimize_top_n

    # Audit: score all skills, rank by quality
    report = audit_all_skills()

    # Optimize: improve the N worst high-frequency skills
    results = optimize_top_n(n=5, iterations_per_skill=5)
"""
import json
import logging
from pathlib import Path
from typing import Optional

from autoresearch import llm_judge, AutoResearchLoop, EvalResult

log = logging.getLogger("skill_audit")

# ---------------------------------------------------------------------------
# Skill loading
# ---------------------------------------------------------------------------

SOUL_SKILLS_DIR = Path(__file__).resolve().parent / "soul" / "learned"
AGENT_DIRS = list(Path(__file__).resolve().parent.parent.glob("*/skills"))

SKILL_CRITERIA = {
    "trigger_clarity": (
        "A reader can immediately tell WHEN to apply this skill — "
        "specific trigger conditions, not vague 'when appropriate'"
    ),
    "actionability": (
        "The skill gives concrete steps or a decision procedure, "
        "not abstract principles or truisms"
    ),
    "information_density": (
        "Every sentence adds new information — no filler, no repetition, "
        "no restating the obvious"
    ),
    "distinctiveness": (
        "The skill teaches something non-obvious that a competent practitioner "
        "might not already know"
    ),
}

SKILL_RUBRIC = """Score anchors for skill definitions:
- 1-2: Platitude or truism ('write clearly', 'test your code')
- 3-4: Has a real idea but buried in generic advice
- 5-6: Useful content but trigger conditions vague or steps unclear
- 7-8: Clear trigger, concrete steps, teaches something real
- 9-10: Immediately applicable, changes how you work, worth memorizing"""


def load_all_skills() -> list[dict]:
    """Load all skills from soul/learned/ and per-agent skill directories.

    Returns list of {name, path, content, source, tags}.
    """
    skills = []

    # Soul skills (central)
    index_path = SOUL_SKILLS_DIR / "index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        for entry in index:
            fpath = SOUL_SKILLS_DIR / entry.get("file", "")
            if fpath.exists():
                skills.append({
                    "name": entry.get("name", fpath.stem),
                    "path": str(fpath),
                    "content": fpath.read_text(encoding="utf-8"),
                    "source": "soul/learned",
                    "tags": entry.get("tags", []),
                    "description": entry.get("description", ""),
                })

    # Per-agent skills
    for agent_dir in AGENT_DIRS:
        agent_name = agent_dir.parent.name
        agent_index = agent_dir / "index.json"
        if agent_index.exists():
            try:
                index = json.loads(agent_index.read_text(encoding="utf-8"))
                for entry in index:
                    fpath = agent_dir / entry.get("file", "")
                    if fpath.exists():
                        skills.append({
                            "name": entry.get("name", fpath.stem),
                            "path": str(fpath),
                            "content": fpath.read_text(encoding="utf-8"),
                            "source": f"agents/{agent_name}/skills",
                            "tags": entry.get("tags", []),
                            "description": entry.get("description", ""),
                        })
            except (json.JSONDecodeError, OSError):
                continue

    # Deduplicate by name
    seen = set()
    unique = []
    for s in skills:
        if s["name"] not in seen:
            seen.add(s["name"])
            unique.append(s)

    return unique


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def audit_skill(skill: dict, judge_fn=None) -> dict:
    """Evaluate a single skill and return scores."""
    result = llm_judge(
        output=skill["content"],
        criteria=SKILL_CRITERIA,
        rubric=SKILL_RUBRIC,
        model_fn=judge_fn,
    )
    return {
        "name": skill["name"],
        "source": skill["source"],
        "tags": skill["tags"],
        "scores": result.scores,
        "aggregate": result.aggregate,
        "reasoning": result.reasoning,
    }


def audit_all_skills(judge_fn=None, save_path: Optional[Path] = None) -> list[dict]:
    """Audit all skills and return ranked results.

    Returns list sorted by aggregate score (worst first).
    """
    skills = load_all_skills()
    log.info("Auditing %d skills...", len(skills))

    results = []
    for i, skill in enumerate(skills):
        log.info("[%d/%d] Auditing: %s", i + 1, len(skills), skill["name"])
        try:
            result = audit_skill(skill, judge_fn)
            results.append(result)
        except Exception as e:
            log.warning("Failed to audit %s: %s", skill["name"], e)
            results.append({
                "name": skill["name"],
                "source": skill["source"],
                "tags": skill["tags"],
                "scores": {},
                "aggregate": 0,
                "reasoning": f"Audit failed: {e}",
            })

    # Sort worst first
    results.sort(key=lambda r: r["aggregate"])

    # Save report
    if save_path is None:
        save_path = Path(__file__).resolve().parent / "autoresearch_runs" / "skill_audit.json"
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("Audit complete. Saved to %s", save_path)

    # Print summary
    print(f"\n{'='*70}")
    print(f"SKILL AUDIT RESULTS ({len(results)} skills)")
    print(f"{'='*70}")
    for r in results[:10]:
        print(f"  {r['aggregate']:5.1f}  {r['name']:40s}  [{r['source']}]")
    print(f"  ...")
    for r in results[-3:]:
        print(f"  {r['aggregate']:5.1f}  {r['name']:40s}  [{r['source']}]")
    avg = sum(r["aggregate"] for r in results) / max(len(results), 1)
    print(f"\n  Average: {avg:.1f}  |  Worst: {results[0]['aggregate']:.1f}  |  Best: {results[-1]['aggregate']:.1f}")

    return results


# ---------------------------------------------------------------------------
# Optimize worst skills
# ---------------------------------------------------------------------------

def optimize_top_n(
    n: int = 5,
    iterations_per_skill: int = 5,
    budget_per_skill: float = 15,
    audit_results: Optional[list[dict]] = None,
    judge_fn=None,
) -> list[dict]:
    """Optimize the N worst skills using the AutoResearch loop.

    Args:
        n: Number of worst skills to optimize
        iterations_per_skill: Max iterations per skill
        budget_per_skill: Minutes per skill
        audit_results: Pre-computed audit results (or will run audit)
    """
    if not audit_results:
        audit_results = audit_all_skills(judge_fn)

    # Take the N worst
    worst = audit_results[:n]
    log.info("Optimizing %d worst skills: %s",
             len(worst), [w["name"] for w in worst])

    # Load full skill content for optimization
    all_skills = {s["name"]: s for s in load_all_skills()}
    results = []

    for w in worst:
        skill = all_skills.get(w["name"])
        if not skill:
            log.warning("Skill not found: %s", w["name"])
            continue

        log.info("Optimizing skill: %s (current score: %.1f)", w["name"], w["aggregate"])

        loop = AutoResearchLoop(
            name=f"skill-{w['name']}",
            eval_fn=lambda asset_text: asset_text,  # Skill IS the output
            criteria=SKILL_CRITERIA,
            directive=(
                f"Improve this skill definition. Current weaknesses: {w['reasoning']}\n\n"
                f"Rules:\n"
                f"- Keep the same topic and core insight\n"
                f"- Make trigger conditions more specific\n"
                f"- Add concrete steps or decision procedures\n"
                f"- Remove filler and generic advice\n"
                f"- Every sentence must teach something non-obvious"
            ),
            asset_path=Path(skill["path"]),
            rubric=SKILL_RUBRIC,
        )

        result = loop.run(
            max_iterations=iterations_per_skill,
            time_budget_minutes=budget_per_skill,
        )
        results.append(result)

        log.info("Skill %s: %.2f → %.2f (+%.2f)",
                 w["name"], result["baseline_score"],
                 result["final_score"], result["score_delta"])

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    parser = argparse.ArgumentParser(description="Audit and optimize Mira skills")
    parser.add_argument("--action", choices=["audit", "optimize"], default="audit")
    parser.add_argument("--top-n", type=int, default=5, help="Number of worst skills to optimize")
    parser.add_argument("--iterations", type=int, default=5, help="Iterations per skill")
    parser.add_argument("--budget", type=float, default=15, help="Minutes per skill")
    args = parser.parse_args()

    if args.action == "audit":
        audit_all_skills()
    elif args.action == "optimize":
        optimize_top_n(n=args.top_n, iterations_per_skill=args.iterations,
                       budget_per_skill=args.budget)
