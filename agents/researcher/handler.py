"""
Researcher agent — iterative deep research until the question is resolved.

Architecture (AutoResearch-inspired):
1. PLAN: Decompose query into sub-questions
2. RESEARCH: For each sub-question, search + summarize (parallel-capable)
3. REFLECT: Evaluate coverage — gaps? contradictions? new angles?
4. ITERATE: If gaps remain and budget allows, refine and research more
5. SYNTHESIZE: Produce final report with citations and confidence levels

Also handles math research (proofs, paper review) via specialized skills.
"""
import json
import logging
import re
import sys
import time
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent
if str(_AGENTS_DIR / "shared") not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR / "shared"))

from config import (
    RESEARCHER_MAX_ITERATIONS, RESEARCHER_MAX_WALL_CLOCK,
    RESEARCHER_MAX_SOURCES, RESEARCHER_SYNTHESIS_TIMEOUT,
    RESEARCHER_PLAN_TIMEOUT, RESEARCHER_QUERY_TIMEOUT,
    RESEARCHER_REFLECT_TIMEOUT,
)

log = logging.getLogger("researcher_agent")

_SKILLS_DIR = Path(__file__).parent / "skills"
_SKILLS_INDEX = _SKILLS_DIR / "index.json"

# Budget limits (from config)
MAX_ITERATIONS = RESEARCHER_MAX_ITERATIONS
MAX_WALL_CLOCK = RESEARCHER_MAX_WALL_CLOCK
MAX_SOURCES_PER_QUESTION = RESEARCHER_MAX_SOURCES


def _load_skills(tags: list[str] | None = None) -> str:
    """Load skill summaries, optionally filtered by tags."""
    if not _SKILLS_INDEX.exists():
        return ""
    index = json.loads(_SKILLS_INDEX.read_text(encoding="utf-8"))
    if tags:
        tag_set = set(tags)
        index = [s for s in index if tag_set & set(s.get("tags", []))]
    return "\n".join(f"- **{s['name']}**: {s['description']}" for s in index)


def _is_math_task(content: str) -> bool:
    """Check if this is a pure math/proof task (not general research)."""
    math_signals = {"proof", "prove", "theorem", "lemma", "conjecture",
                    "integral", "derivative", "equation", "证明", "定理"}
    lower = content.lower()
    return sum(1 for s in math_signals if s in lower) >= 2


def _local_research_question(question: str, claude_think) -> str:
    """Fallback research path when Claude tool mode cannot browse/write files."""
    from web_browser import read_article, search

    query = re.sub(r"\s+", " ", question).strip()[:200]
    results = search(query, max_results=min(MAX_SOURCES_PER_QUESTION, 5))
    if not results:
        return ""

    source_blocks = []
    for i, result in enumerate(results[:MAX_SOURCES_PER_QUESTION], 1):
        page = read_article(result.url)
        excerpt = page.summary(1600) if page.ok else result.snippet
        source_blocks.append(
            f"""## Source {i}
Title: {result.title}
URL: {result.url}
Snippet: {result.snippet}

Excerpt:
{excerpt}"""
        )

    prompt = f"""You are Mira's researcher agent. Tool-mode browsing is unavailable, so another system has gathered source material for you.

## Research Question
{question}

## Source Pack
{chr(10).join(source_blocks)}

## Task
Write a research memo that:
- summarizes the key findings with specific facts, dates, and names
- includes inline source citations as markdown links using the supplied titles/URLs
- explicitly notes contradictions or uncertainty
- is concise but information-dense

Markdown only.
"""
    return (claude_think(prompt, timeout=RESEARCHER_QUERY_TIMEOUT, tier="light") or "").strip()


def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str,
           thread_history: str = "", thread_memory: str = "") -> str | None:
    """Handle a research task with iterative deep-dive."""
    import sys
    shared_dir = str(Path(__file__).parent.parent / "shared")
    if shared_dir not in sys.path:
        sys.path.insert(0, shared_dir)

    from soul_manager import load_soul, format_soul
    from sub_agent import claude_think, claude_act

    soul = load_soul()
    soul_ctx = format_soul(soul)
    skills_ctx = _load_skills()

    # Math tasks use single-pass deep thinking (proofs don't need web search)
    if _is_math_task(content):
        return _handle_math(workspace, content, soul_ctx, skills_ctx,
                           thread_history, thread_memory, claude_think)

    # General research: iterative plan → search → reflect loop
    return _handle_research(workspace, task_id, content, soul_ctx, skills_ctx,
                           thread_history, claude_think, claude_act)


def _handle_math(workspace, content, soul_ctx, skills_ctx,
                 thread_history, thread_memory, claude_think) -> str | None:
    """Single-pass deep math reasoning."""
    thread_ctx = ""
    if thread_history:
        thread_ctx = f"\n## Conversation so far\n{thread_history}\n"
    if thread_memory:
        thread_ctx += f"\n## Thread memory\n{thread_memory}\n"

    prompt = f"""{soul_ctx}

## Research Skills
{skills_ctx}
{thread_ctx}
## Task
{content}

---
Be rigorous: state assumptions, distinguish proved from conjectured,
flag gaps. Use LaTeX where appropriate."""

    result = claude_think(prompt, timeout=RESEARCHER_SYNTHESIS_TIMEOUT, tier="heavy")
    if result:
        (workspace / "output.md").write_text(result, encoding="utf-8")
    return result


def _handle_research(workspace, task_id, content, soul_ctx, skills_ctx,
                     thread_history, claude_think, claude_act) -> str | None:
    """Iterative deep research loop."""
    start_time = time.monotonic()
    knowledge_base = []  # list of {question, findings, sources}
    iteration = 0

    # --- Phase 1: PLAN ---
    plan_prompt = f"""You are a research planner. Break this query into 3-6 specific sub-questions
that collectively answer it. Each sub-question should be answerable via web search.

Query: {content}

Return as JSON array of strings. Example:
["What is X?", "How does X compare to Y?", "What are the latest developments in X?"]

JSON only, no other text."""

    plan_raw = claude_think(plan_prompt, timeout=RESEARCHER_PLAN_TIMEOUT, tier="light")
    try:
        sub_questions = json.loads(plan_raw.strip().strip("```json").strip("```"))
    except (json.JSONDecodeError, ValueError):
        # Fallback: treat the whole query as one question
        sub_questions = [content]

    log.info("Research plan: %d sub-questions for task %s", len(sub_questions), task_id)
    (workspace / "plan.json").write_text(
        json.dumps(sub_questions, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Phase 2+3: RESEARCH + REFLECT loop ---
    remaining_questions = list(sub_questions)

    while remaining_questions and iteration < MAX_ITERATIONS:
        elapsed = time.monotonic() - start_time
        if elapsed > MAX_WALL_CLOCK:
            log.info("Research budget exhausted (%.0fs)", elapsed)
            break

        iteration += 1
        batch = remaining_questions[:3]  # process up to 3 questions per iteration
        remaining_questions = remaining_questions[3:]

        for question in batch:
            log.info("Researching [iter %d]: %s", iteration, question[:60])

            research_prompt = f"""Research this question thoroughly using web search.

Question: {question}

Instructions:
- Search for recent, authoritative sources
- Summarize key findings with source URLs
- Note any contradictions between sources
- Be specific — include data, dates, names

Write findings to output.md in the workspace."""

            result = claude_act(research_prompt, cwd=workspace, timeout=RESEARCHER_QUERY_TIMEOUT, tier="light")
            if not result:
                log.warning("Research tool path unavailable for question '%s' — using local web fallback",
                            question[:80])
                result = _local_research_question(question, claude_think)
            if result:
                knowledge_base.append({
                    "question": question,
                    "findings": result,
                    "iteration": iteration,
                })

        # --- REFLECT: check coverage ---
        if remaining_questions or iteration < MAX_ITERATIONS:
            kb_summary = "\n\n".join(
                f"Q: {item['question']}\nA: {item['findings'][:500]}"
                for item in knowledge_base
            )
            reflect_prompt = f"""You are reviewing research progress.

Original query: {content}

Knowledge gathered so far:
{kb_summary}

Remaining planned questions: {json.dumps(remaining_questions, ensure_ascii=False)}

Are there important gaps? New questions that emerged from the findings?
If the query is sufficiently answered, respond with: DONE
Otherwise, respond with a JSON array of 1-3 NEW sub-questions to investigate.
JSON or "DONE", nothing else."""

            reflect_raw = claude_think(reflect_prompt, timeout=RESEARCHER_REFLECT_TIMEOUT, tier="light")
            if "DONE" in reflect_raw.upper():
                log.info("Research converged at iteration %d", iteration)
                break
            try:
                new_questions = json.loads(reflect_raw.strip().strip("```json").strip("```"))
                if isinstance(new_questions, list) and new_questions:
                    remaining_questions.extend(new_questions)
                    log.info("Reflect added %d new questions", len(new_questions))
            except (json.JSONDecodeError, ValueError):
                pass  # No new questions, continue

    # --- Phase 4: SYNTHESIZE ---
    kb_full = "\n\n---\n\n".join(
        f"## {item['question']}\n{item['findings']}"
        for item in knowledge_base
    )

    synth_prompt = f"""{soul_ctx}

## Research Skills
{skills_ctx}

## Original Query
{content}

## Research Findings ({len(knowledge_base)} questions investigated, {iteration} iterations)
{kb_full}

## Task
Synthesize the research findings into a comprehensive report:
- Use the user's language (Chinese if query is Chinese)
- Include inline source citations [title](url)
- Mark confidence: strong (multiple sources agree), moderate (single source), uncertain
- Structure with clear headings
- End with key takeaways and open questions
- Markdown format"""

    report = claude_think(synth_prompt, timeout=RESEARCHER_SYNTHESIS_TIMEOUT, tier="heavy")
    if not report:
        # Fallback: return raw findings
        report = f"# Research: {content}\n\n{kb_full}"

    (workspace / "output.md").write_text(report, encoding="utf-8")

    elapsed = time.monotonic() - start_time
    log.info("Research complete: %d questions, %d iterations, %.0fs, task %s",
             len(knowledge_base), iteration, elapsed, task_id)

    return report
