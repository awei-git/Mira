"""
Math agent handler.
Handles mathematical research tasks: proof assistance, literature synthesis,
conjecture testing, asymptotic analysis, and paper writing/review.
"""
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent / "skills"
_SKILLS_INDEX = _SKILLS_DIR / "index.json"


def _load_skills(tags: list[str] | None = None) -> str:
    """Load skill summaries, optionally filtered by tags."""
    if not _SKILLS_INDEX.exists():
        return ""
    index = json.loads(_SKILLS_INDEX.read_text(encoding="utf-8"))
    if tags:
        tag_set = set(tags)
        index = [s for s in index if tag_set & set(s.get("tags", []))]
    lines = [f"- **{s['name']}**: {s['description']}" for s in index]
    return "\n".join(lines)


def _load_skill_detail(name: str) -> str:
    """Load the full content of a named skill file."""
    if not _SKILLS_INDEX.exists():
        return ""
    index = json.loads(_SKILLS_INDEX.read_text(encoding="utf-8"))
    for entry in index:
        if entry["name"].lower() == name.lower():
            skill_file = _SKILLS_DIR / entry["file"]
            if skill_file.exists():
                return skill_file.read_text(encoding="utf-8")
    return ""


def _select_relevant_skills(content: str) -> str:
    """Return full content of skills most relevant to this task."""
    content_lower = content.lower()
    selected = []

    keyword_map = {
        "proof": ["Proof Strategy Selection", "Advanced Proof Tactics"],
        "prove": ["Proof Strategy Selection", "Advanced Proof Tactics"],
        "asymptot": ["Asymptotic Analysis"],
        "conjecture": ["Conjecture Formulation and Testing", "Computational Verification"],
        "gaussian": ["Gaussian Moment Inequalities", "Stochastic Process Fundamentals"],
        "random": ["Random Zeros and Rice Formula", "Stochastic Process Fundamentals"],
        "rice": ["Random Zeros and Rice Formula"],
        "kac": ["Random Zeros and Rice Formula"],
        "spde": ["SPDE Uniqueness Techniques"],
        "stochastic": ["SPDE Uniqueness Techniques", "Stochastic Process Fundamentals"],
        "spin glass": ["Spin Glass Models and Gaussian Bounds"],
        "small deviation": ["Small Deviation Probabilities"],
        "paper": ["Math Paper Writing", "Reading Mathematics Papers"],
        "write": ["Math Paper Writing"],
        "read": ["Reading Mathematics Papers", "Literature Synthesis"],
        "literature": ["Literature Synthesis"],
        "referee": ["Math Paper Writing"],
        "generali": ["Abstraction and Generalization"],
        "abstract": ["Abstraction and Generalization"],
        "heuristic": ["Mathematical Problem-Solving Heuristics"],
        "stuck": ["Mathematical Problem-Solving Heuristics"],
        "verif": ["Computational Verification"],
    }

    seen = set()
    for keyword, skill_names in keyword_map.items():
        if keyword in content_lower:
            for name in skill_names:
                if name not in seen:
                    detail = _load_skill_detail(name)
                    if detail:
                        selected.append(detail)
                        seen.add(name)

    # Always include problem-solving heuristics as a fallback
    if not selected:
        detail = _load_skill_detail("Mathematical Problem-Solving Heuristics")
        if detail:
            selected.append(detail)

    return "\n\n---\n\n".join(selected)


def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str,
           thread_history: str = "", thread_memory: str = "") -> str | None:
    """Handle a math research task."""
    import sys
    shared_dir = str(Path(__file__).parent.parent / "shared")
    if shared_dir not in sys.path:
        sys.path.insert(0, shared_dir)

    from soul_manager import load_soul, format_soul
    from sub_agent import claude_think

    soul = load_soul()
    soul_ctx = format_soul(soul)
    skills_ctx = _select_relevant_skills(content)
    skills_summary = _load_skills()

    thread_ctx = ""
    if thread_history:
        thread_ctx = f"\n## Conversation so far\n{thread_history}\n"
    if thread_memory:
        thread_ctx += f"\n## Thread memory\n{thread_memory}\n"

    prompt = f"""{soul_ctx}

## Your Math Research Skills
{skills_summary}

## Relevant Skill Details
{skills_ctx}
{thread_ctx}
## Task
{content}

---

You are assisting with a math research task. Apply the relevant skills above.
Be rigorous: state assumptions clearly, distinguish proved results from conjectures,
and flag any gaps in reasoning. Use LaTeX notation where appropriate.
If writing or reviewing a proof, follow the Math Paper Writing skill conventions.
If the task requires computational verification, suggest concrete checks.
"""

    result = claude_think(prompt, timeout=180, tier="heavy")
    if not result:
        return None

    (workspace / "output.md").write_text(result, encoding="utf-8")
    return result[:400]
