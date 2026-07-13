"""self_iteration.py — Autonomous self-improvement pipeline.

Converts experience into durable knowledge:
- Task failures → operational rules (prevent recurrence)
- Completed articles → craft skills (extract writing techniques used)
- Recurring patterns → skill upgrades (strengthen weak areas)

Called from:
- do_journal() — daily post-mortem on today's failures and completions
- writing_workflow._finalize() — extract craft skills from finished articles
- task_worker._write_result() — extract lessons from failed tasks
"""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from config import (
    EPISODES_DIR,
    JOURNAL_DIR,
    SOUL_DIR,
    SKILLS_DIR,
    WRITINGS_OUTPUT_DIR,
    CATALOG_FILE,
)

log = logging.getLogger("mira.self_iteration")
_SKILL_CANDIDATES_DIR = SOUL_DIR / "skill_candidates"


# ---------------------------------------------------------------------------
# Post-mortem: extract lessons from failures
# ---------------------------------------------------------------------------


def extract_failure_lesson(task_id: str, title: str, error_context: str, claude_fn=None) -> dict | None:
    """Analyze a failed task and extract a preventive rule or skill.

    Args:
        task_id: The failed task ID
        title: Task title
        error_context: What went wrong (error message, traceback, context)
        claude_fn: Function to call Claude (signature: fn(prompt, timeout) -> str)

    Returns:
        Dict with {type: "rule"|"skill", name, content} or None
    """
    if not claude_fn:
        try:
            from llm import claude_think

            claude_fn = claude_think
        except ImportError:
            log.warning("self_iteration: no claude_fn available")
            return None

    prompt = f"""Analyze this task failure and extract a reusable lesson.

Task: {title} (id: {task_id})
Error context:
{error_context[:2000]}

Determine if this failure reveals:
1. An **operational rule** — something that should always/never be done (e.g. "always check X before Y")
2. A **skill gap** — a technique or knowledge that would have prevented this

Output JSON only:
{{
    "worth_extracting": true/false,
    "type": "rule" or "skill",
    "name": "short-kebab-case-name",
    "description": "one-line description",
    "content": "The full rule or skill content (markdown, 100-300 words). Be specific and actionable.",
    "tags": ["category1", "category2"],
    "reason": "Why this is worth remembering"
}}

If the failure is trivial (typo, transient network error, etc.), set worth_extracting to false.
"""
    result = claude_fn(prompt, timeout=60)
    if not result:
        return None

    try:
        # Extract JSON from response
        json_match = re.search(r"\{[\s\S]+\}", result)
        if not json_match:
            return None
        data = json.loads(json_match.group())
        if not data.get("worth_extracting"):
            log.info("Failure lesson not worth extracting: %s", title)
            return None
        return data
    except (json.JSONDecodeError, KeyError):
        log.warning("Failed to parse failure lesson output")
        return None


def save_failure_lesson(lesson: dict) -> bool:
    """Security-audit and queue a failure lesson for a later reuse trial."""
    name = lesson["name"]
    content = f"""# {name}

{lesson.get('description', '')}

**Source**: Extracted from task failure ({datetime.now().strftime('%Y-%m-%d')})
**Tags**: {', '.join(lesson.get('tags', []))}

---

{lesson.get('content', '')}
"""
    queued = queue_skill_candidates(
        [
            {
                **lesson,
                "content": content,
                "validation_test": "Apply to a later comparable failure and verify non-recurrence.",
            }
        ],
        source_title=f"task failure: {name}",
    )
    if queued:
        log.info("Queued failure lesson as a skill candidate: %s", name)
    else:
        log.warning("Failure lesson '%s' was not queued; security audit rejected it", name)
    return bool(queued)


# ---------------------------------------------------------------------------
# Article distillation: extract craft skills from completed writing
# ---------------------------------------------------------------------------


def distill_article_skills(title: str, final_text: str, type_key: str = "essay", claude_fn=None) -> list[dict]:
    """Analyze a completed article and extract reusable writing craft skills.

    Args:
        title: Article title
        final_text: The final article text
        type_key: Writing type (essay, blog, novel, etc.)
        claude_fn: Function to call Claude

    Returns:
        List of skill dicts [{name, description, content, tags}]
    """
    if not claude_fn:
        try:
            from llm import claude_think

            claude_fn = claude_think
        except ImportError:
            return []

    # Check existing writer skills to avoid duplicates
    existing_skills = set()
    from config import MIRA_ROOT as _MR

    writer_skills_dir = _MR / "agents" / "writer" / "skills"
    if writer_skills_dir.exists():
        for f in writer_skills_dir.glob("*.md"):
            existing_skills.add(f.stem)
    if SKILLS_DIR.exists():
        for f in SKILLS_DIR.glob("*.md"):
            existing_skills.add(f.stem)

    prompt = f"""Analyze this completed {type_key} and extract any reusable writing craft techniques.

Title: {title}
Text (first 3000 chars):
{final_text[:3000]}

Existing skills (DO NOT duplicate these): {', '.join(sorted(existing_skills)[:30])}

For each genuinely new technique worth remembering, output a JSON array:
[
  {{
    "name": "kebab-case-name",
    "description": "one-line description of the technique",
    "content": "Detailed description of the technique: what it is, when to use it, how to execute it. 100-200 words. Be specific enough to be actionable.",
    "tags": ["writing", "craft", ...],
    "validation_test": "How to test this technique in a different artifact and compare review outcomes"
  }}
]

Rules:
- Only extract techniques that are ACTUALLY demonstrated in this article, not generic advice
- Skip if all techniques are already in existing skills
- Output [] if nothing new worth extracting
- Max 2 skills per article
"""
    result = claude_fn(prompt, timeout=90)
    if not result:
        return []

    try:
        json_match = re.search(r"\[[\s\S]*\]", result)
        if not json_match:
            return []
        skills = json.loads(json_match.group())
        return [s for s in skills if isinstance(s, dict) and s.get("name")]
    except (json.JSONDecodeError, KeyError):
        return []


def save_article_skills(skills: list[dict], source_title: str):
    """Save extracted article skills to the writer skills directory.

    Routes through save_skill() for security audit and quality gate.
    Only writes per-agent copy if the skill passes.
    """
    from memory.soul_skills import save_skill

    from config import MIRA_ROOT as _MR

    writer_skills_dir = _MR / "agents" / "writer" / "skills"
    writer_skills_dir.mkdir(parents=True, exist_ok=True)

    for skill in skills:
        name = skill["name"]
        slug = name.lower().replace(" ", "-")
        path = writer_skills_dir / f"{slug}.md"
        if path.exists():
            log.info("Writer skill already exists, skipping: %s", name)
            continue
        description = skill.get("description", "")
        content = f"""# {name}

{description}

**Extracted from**: "{source_title}" ({datetime.now().strftime('%Y-%m-%d')})
**Tags**: {', '.join(skill.get('tags', []))}

---

{skill.get('content', '')}
"""
        # Route through quality gate first
        if not save_skill(name, description, content):
            log.warning("Writer skill '%s' rejected by quality gate, skipping", name)
            continue

        # Only write per-agent copy after gate passes
        path.write_text(content, encoding="utf-8")
        log.info("Saved writer craft skill: %s (from '%s')", name, source_title)


def queue_skill_candidates(skills: list[dict], source_title: str) -> list[dict]:
    """Audit and queue observed techniques without enabling them as skills.

    One successful article is evidence that a technique appeared, not that it
    generalizes. Promotion stays explicit and will pass through ``save_skill``
    (and therefore a second security audit) after a reuse trial succeeds.
    """
    from memory.soul_skills import SkillAuditFailedError, audit_skill

    queued: list[dict] = []
    for skill in skills:
        name = str(skill.get("name") or "").strip()
        if not name:
            continue
        content = str(skill.get("content") or "").strip()
        try:
            audit = audit_skill(name, content, source="agent_generated")
        except SkillAuditFailedError as exc:
            log.warning("Skill candidate '%s' failed security audit: %s", name, exc)
            continue
        if audit.get("requires_review") or audit.get("passed") is False:
            log.warning("Skill candidate '%s' requires security review; not queued", name)
            continue

        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "skill"
        candidate_path = _SKILL_CANDIDATES_DIR / f"{slug}.json"
        previous: dict = {}
        if candidate_path.exists():
            try:
                previous = json.loads(candidate_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                previous = {}
        observations = list(previous.get("observations") or [])
        observations.append({"source_title": source_title, "observed_at": datetime.now().isoformat()})

        candidate = {
            "name": name,
            "description": str(skill.get("description") or ""),
            "content": content,
            "tags": list(skill.get("tags") or []),
            "source_title": source_title,
            "status": "candidate",
            "created_at": previous.get("created_at") or datetime.now().isoformat(),
            "observations": observations,
            "validation_test": str(
                skill.get("validation_test") or "Use in a second artifact and compare review outcomes."
            ),
            "security_audit": {"passed": True, "result": audit.get("result", "PASS")},
        }
        _SKILL_CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
        queued.append(candidate)
    return queued


def queue_article_skill_candidates(skills: list[dict], source_title: str) -> list[dict]:
    """Backward-compatible named route for writing craft candidates."""
    return queue_skill_candidates(skills, source_title)


# ---------------------------------------------------------------------------
# Daily post-mortem: run during journal cycle
# ---------------------------------------------------------------------------


def daily_postmortem(claude_fn=None) -> str:
    """Review today's episodes for failures and extract lessons.

    Returns a summary of what was extracted (for journal context).
    """
    if not EPISODES_DIR.exists():
        return ""

    today = datetime.now().strftime("%Y-%m-%d")
    extracted = []

    for path in EPISODES_DIR.glob(f"{today}_*.md"):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue

        # Look for failure indicators
        has_failure = any(
            kw in content.lower()
            for kw in [
                "error",
                "failed",
                "exception",
                "traceback",
                "bug",
                "wrong",
                "mistake",
                "不对",
                "出错",
                "失败",
            ]
        )
        if not has_failure:
            continue

        # Extract task_id from episode header
        task_match = re.search(r"Task:\s*(\S+)", content)
        task_id = task_match.group(1) if task_match else path.stem
        title_match = re.search(r"# Episode:\s*(.+)", content)
        title = title_match.group(1) if title_match else path.stem

        lesson = extract_failure_lesson(task_id, title, content[:2000], claude_fn)
        if lesson:
            save_failure_lesson(lesson)
            extracted.append(f"- {lesson['name']}: {lesson.get('description', '')}")

    if extracted:
        summary = f"Extracted {len(extracted)} lessons from today's failures:\n" + "\n".join(extracted)
        log.info(summary)
        return summary
    return ""


# ---------------------------------------------------------------------------
# Article completion hook: run after writing pipeline finishes
# ---------------------------------------------------------------------------


def on_article_complete(title: str, final_text: str, type_key: str = "essay", claude_fn=None):
    """Hook called after an article is finalized. Queues craft-skill trials."""
    try:
        skills = distill_article_skills(title, final_text, type_key, claude_fn)
        if skills:
            queued = queue_article_skill_candidates(skills, title)
            log.info("Queued %d/%d craft-skill candidates from '%s'", len(queued), len(skills), title)
    except Exception as e:
        log.warning("Article skill distillation failed for '%s': %s", title, e)
