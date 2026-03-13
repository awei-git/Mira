"""Multi-agent writing workflow with plan/write/review/converge cycles.

Phases:
    analyze        Determine type, complexity, criteria
    plan           Multi-agent discussion to create writing plan
    await_plan     Wait for user to approve/edit plan (via Apple Notes)
    write          3+ agents write drafts following the plan
    review         3+ reviewers score and critique (5+ rounds)
    await_feedback Wait for user feedback on converged draft
    revise         Revise based on feedback (loops back to review)
    done           Final version complete

Versioning:
    workspace/{slug}/
        project.json        State and metadata
        analysis.json       Type classification
        v1/
            plans/          Agent discussion (propose/critique/synthesize)
            plan_approved.md
            drafts/         One per writer model
            reviews/        round_01.json .. round_05.json
            revisions/      Revised drafts per round
            converged.md    Final draft for this version
        v2/                 After user feedback
            ...
        final.md            Finalized output
"""
import json
import logging
import random
import re
from datetime import datetime
from pathlib import Path

from config import (
    WORKSPACE_DIR, NOTES_INBOX_FOLDER, NOTES_OUTPUT_FOLDER,
    MODELS, WRITING_MODELS, REVIEW_MODELS, WRITING_CRITERIA,
    MIN_REVIEW_ROUNDS, CLAUDE_TIMEOUT_PLAN,
)
from sub_agent import model_think, claude_think
from notes_bridge import create_note, fetch_notes
from soul_manager import load_soul, format_soul, append_memory
from prompts import (
    analyze_writing_prompt, plan_propose_prompt, plan_critique_prompt,
    plan_synthesize_prompt, write_draft_prompt, review_draft_prompt,
    revise_draft_prompt, revise_with_feedback_prompt,
    chapter_write_prompt, harsh_review_prompt,
)

log = logging.getLogger("mira")


# ---------------------------------------------------------------------------
# Project state helpers
# ---------------------------------------------------------------------------

def _load_project(ws: Path) -> dict:
    f = ws / "project.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def _save_project(ws: Path, p: dict):
    p["updated"] = datetime.now().isoformat()
    (ws / "project.json").write_text(
        json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8",
    )


def _vdir(ws: Path, v: int) -> Path:
    d = ws / "versions" / f"v{v}"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# START — analyze idea, plan via multi-agent discussion, post for approval
# ---------------------------------------------------------------------------

def start_project(title: str, body: str, workspace: Path):
    """Initialize a writing project: ANALYZE -> PLAN -> post for user approval."""
    log.info("Starting writing project: %s", title)
    workspace.mkdir(parents=True, exist_ok=True)

    # --- Analyze ---
    analysis = _analyze(body)
    (workspace / "analysis.json").write_text(
        json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    type_key = analysis.get("type", "essay")
    type_info = WRITING_CRITERIA.get(type_key, WRITING_CRITERIA["essay"])
    criteria = type_info["criteria"]

    # --- Plan (multi-agent: propose -> critique -> synthesize) ---
    soul_ctx = format_soul(load_soul())
    vd = _vdir(workspace, 1)
    plans_dir = vd / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    merged = _plan(soul_ctx, analysis, body, plans_dir)
    (plans_dir / "merged.md").write_text(merged, encoding="utf-8")

    # --- Save project state ---
    project = {
        "title": title,
        "idea": body,
        "type": type_key,
        "type_name": type_info["name"],
        "phase": "await_plan",
        "version": 1,
        "criteria": criteria,
        "analysis": analysis,
        "created": datetime.now().isoformat(),
    }
    _save_project(workspace, project)

    # --- Post plan to Apple Notes for user review ---
    criteria_names = ", ".join(criteria.keys())
    note_body = (
        f"Status: wip\n\n"
        f"# 写作计划: {title}\n\n"
        f"类型: {type_info['name']} | 语言: {analysis.get('language', '?')}\n"
        f"评估标准: {criteria_names}\n\n---\n\n"
        f"{merged}\n\n---\n\n"
        f"请审阅并编辑上面的计划。完成后将 Status 改为 done。"
    )
    create_note(NOTES_INBOX_FOLDER, f"计划: {title}", note_body)
    append_memory(
        f"Writing project '{title}' [{type_key}] started — plan posted for review"
    )
    log.info("Plan posted for user review: %s", title)


def _analyze(body: str) -> dict:
    """Ask Claude to classify the writing type and parameters."""
    result = claude_think(analyze_writing_prompt(body), timeout=CLAUDE_TIMEOUT_PLAN, tier="heavy")
    try:
        m = re.search(r"\{[^{}]*\}", result, re.DOTALL)
        analysis = json.loads(m.group()) if m else json.loads(result)
    except Exception:
        log.warning("Failed to parse analysis JSON, using defaults")
        analysis = {
            "type": "essay", "complexity": "medium",
            "language": "zh", "suggested_word_count": 3000,
        }

    t = analysis.get("type", "essay")
    if t not in WRITING_CRITERIA:
        t = "essay"
    analysis["type"] = t
    analysis["type_name"] = WRITING_CRITERIA[t]["name"]
    analysis["criteria"] = WRITING_CRITERIA[t]["criteria"]
    return analysis


def _plan(soul_ctx: str, analysis: dict, idea: str, plans_dir: Path) -> str:
    """Multi-agent planning: propose -> critique -> synthesize."""
    agents = WRITING_MODELS[:3]

    # Agent A proposes
    style_a = MODELS.get(agents[0], {}).get("style", "")
    plan_a = model_think(
        plan_propose_prompt(soul_ctx, analysis, idea, style_a),
        model_name=agents[0], timeout=CLAUDE_TIMEOUT_PLAN,
    ) or ""
    (plans_dir / f"plan_{agents[0]}.md").write_text(plan_a, encoding="utf-8")
    log.info("Plan proposed by %s: %d chars", agents[0], len(plan_a))

    # Agent B critiques and counter-proposes
    style_b = MODELS.get(agents[1], {}).get("style", "")
    critique = model_think(
        plan_critique_prompt(soul_ctx, analysis, idea, plan_a, style_b),
        model_name=agents[1], timeout=CLAUDE_TIMEOUT_PLAN,
    ) or ""
    (plans_dir / f"plan_{agents[1]}.md").write_text(critique, encoding="utf-8")
    log.info("Plan critiqued by %s: %d chars", agents[1], len(critique))

    # Agent C synthesizes final plan
    merged = model_think(
        plan_synthesize_prompt(soul_ctx, idea, plan_a, critique),
        model_name=agents[2], timeout=CLAUDE_TIMEOUT_PLAN,
    )
    log.info("Plan synthesized by %s: %d chars", agents[2], len(merged or ""))
    return merged or plan_a or "Plan generation failed."


# ---------------------------------------------------------------------------
# ADVANCE — move project forward based on current phase
# ---------------------------------------------------------------------------

def advance_project(workspace: Path, user_input: str = "") -> str:
    """Advance a writing project. Returns new phase."""
    p = _load_project(workspace)
    if not p:
        log.error("No project.json in %s", workspace)
        return ""

    phase = p["phase"]
    title = p["title"]
    log.info("Advancing '%s' from phase: %s", title, phase)

    if phase == "await_plan":
        return _on_plan_approved(workspace, p, user_input)
    elif phase == "await_feedback":
        return _on_feedback(workspace, p, user_input)
    else:
        log.warning("Project '%s' in non-advanceable phase: %s", title, phase)
        return phase


def _on_plan_approved(ws: Path, p: dict, plan_text: str) -> str:
    """Plan approved -> WRITE -> REVIEW (5 rounds) -> post draft for feedback."""
    title = p["title"]
    v = p["version"]
    vd = _vdir(ws, v)
    criteria = p["criteria"]
    soul_ctx = format_soul(load_soul())

    # Save user-approved plan
    (vd / "plan_approved.md").write_text(plan_text, encoding="utf-8")

    # --- Write (3+ agents) ---
    p["phase"] = "writing"
    _save_project(ws, p)

    writers = WRITING_MODELS[:max(3, len(WRITING_MODELS))]
    drafts = _write_drafts(soul_ctx, plan_text, p["idea"], vd, writers)
    if not drafts:
        log.error("All writers failed for '%s'", title)
        p["phase"] = "error"
        _save_project(ws, p)
        return "error"

    # --- Review cycle (5+ rounds) ---
    p["phase"] = "reviewing"
    _save_project(ws, p)

    reviewers = REVIEW_MODELS[:max(3, len(REVIEW_MODELS))]
    final_draft = _review_cycle(vd, drafts, criteria, reviewers)
    (vd / "converged.md").write_text(final_draft, encoding="utf-8")

    # --- Post draft for user feedback ---
    p["phase"] = "await_feedback"
    _save_project(ws, p)

    preview = final_draft[:8000]
    note_body = (
        f"Status: wip\n\n"
        f"# 初稿: {title}\n\n"
        f"版本 {v} | 经过 {MIN_REVIEW_ROUNDS} 轮评审\n\n"
        f"{preview}\n\n---\n\n"
        f"## 反馈\n"
        f"请在下方写反馈，完成后将 Status 改为 done。\n"
        f"写 \"完成\" 表示满意，可以定稿。"
    )
    create_note(NOTES_INBOX_FOLDER, f"初稿: {title}", note_body)

    append_memory(
        f"Writing '{title}' v{v}: {len(drafts)} drafts, "
        f"{MIN_REVIEW_ROUNDS} review rounds, converged. Awaiting feedback."
    )
    log.info("Draft posted for feedback: %s (v%d)", title, v)
    return "await_feedback"


def _on_feedback(ws: Path, p: dict, feedback: str) -> str:
    """Handle user feedback: finalize if satisfied, else revise and re-review."""
    title = p["title"]

    # Check if user is satisfied
    done_signals = ["完成", "done", "满意", "定稿", "ok", "好了", "可以了", "finalize"]
    if any(s in feedback.lower() for s in done_signals):
        return _finalize(ws, p)

    # --- New version ---
    p["version"] += 1
    v = p["version"]
    vd = _vdir(ws, v)
    (vd / "user_feedback.md").write_text(feedback, encoding="utf-8")

    # Load previous converged draft
    prev_draft = (ws / "versions" / f"v{v - 1}" / "converged.md").read_text(encoding="utf-8")
    criteria = p["criteria"]

    # --- Revise based on feedback ---
    p["phase"] = "revising"
    _save_project(ws, p)

    revised = model_think(
        revise_with_feedback_prompt(prev_draft, feedback, criteria),
        model_name="claude", timeout=300,
    )
    if not revised:
        revised = prev_draft  # fallback: keep previous

    drafts_dir = vd / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    (drafts_dir / "revised.md").write_text(revised, encoding="utf-8")

    # --- Re-review the revision ---
    p["phase"] = "reviewing"
    _save_project(ws, p)

    reviewers = REVIEW_MODELS[:max(3, len(REVIEW_MODELS))]
    final_draft = _review_cycle(vd, {"revised": revised}, criteria, reviewers)
    (vd / "converged.md").write_text(final_draft, encoding="utf-8")

    # --- Post updated draft ---
    p["phase"] = "await_feedback"
    _save_project(ws, p)

    preview = final_draft[:8000]
    note_body = (
        f"Status: wip\n\n"
        f"# 修订稿: {title}\n\n"
        f"版本 {v} | 基于您的反馈修订\n\n"
        f"{preview}\n\n---\n\n"
        f"## 反馈\n"
        f"请在下方写反馈，完成后将 Status 改为 done。\n"
        f"写 \"完成\" 表示满意，可以定稿。"
    )
    create_note(NOTES_INBOX_FOLDER, f"初稿: {title}", note_body)

    append_memory(f"Writing '{title}' v{v}: revised from feedback, re-reviewed")
    log.info("Revised draft posted: %s (v%d)", title, v)
    return "await_feedback"


def _finalize(ws: Path, p: dict) -> str:
    """Produce final version."""
    title = p["title"]
    v = p["version"]
    final_text = (ws / "versions" / f"v{v}" / "converged.md").read_text(encoding="utf-8")

    (ws / "final.md").write_text(f"# {title}\n\n{final_text}", encoding="utf-8")

    p["phase"] = "done"
    _save_project(ws, p)

    create_note(
        NOTES_OUTPUT_FOLDER,
        f"完成: {title}",
        f"'{title}' 已定稿 (v{v})。\n\n{final_text[:3000]}",
    )
    append_memory(f"Writing project '{title}' finalized at v{v}")
    log.info("Project finalized: %s (v%d)", title, v)

    # --- Self-evaluation: score this piece ---
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
        from evaluator import evaluate_writing, record_event
        # Gather review scores from last round
        review_scores = []
        reviews_dir = ws / "versions" / f"v{v}" / "reviews"
        if reviews_dir.exists():
            for rf in sorted(reviews_dir.glob("round_*.json"), reverse=True)[:1]:
                try:
                    rd = json.loads(rf.read_text(encoding="utf-8"))
                    review_scores = [float(s) for s in rd.get("scores", []) if s]
                except (json.JSONDecodeError, ValueError):
                    pass
        w_scores = evaluate_writing(review_scores, final_text[:4000],
                                     {"title": title, "version": v})
        if w_scores:
            record_event("publish", w_scores, {"title": title})
    except Exception as e:
        import logging
        logging.getLogger("writing").warning("Writing self-evaluation failed: %s", e)

    return "done"


# ---------------------------------------------------------------------------
# WRITE — multiple agents produce drafts
# ---------------------------------------------------------------------------

def _write_drafts(soul_ctx: str, plan: str, idea: str,
                  vd: Path, writers: list[str]) -> dict[str, str]:
    """Have 3+ agents write drafts following the plan (parallel)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    drafts_dir = vd / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)

    def _write_one(model_name: str) -> tuple[str, str]:
        style = MODELS.get(model_name, {}).get("style", "")
        log.info("Writing draft: %s (%s)", model_name, style)
        draft = model_think(
            write_draft_prompt(soul_ctx, plan, idea, style),
            model_name=model_name, timeout=600,
        )
        return model_name, draft or ""

    drafts = {}
    with ThreadPoolExecutor(max_workers=len(writers)) as pool:
        futures = {pool.submit(_write_one, w): w for w in writers}
        for fut in as_completed(futures):
            model_name, draft = fut.result()
            if draft:
                drafts[model_name] = draft
                (drafts_dir / f"draft_{model_name}.md").write_text(
                    draft, encoding="utf-8",
                )
                log.info("Draft from %s: %d chars", model_name, len(draft))
            else:
                log.warning("Draft failed from %s", model_name)

    return drafts


# ---------------------------------------------------------------------------
# REVIEW — iterative review/revise cycle
# ---------------------------------------------------------------------------

def _review_cycle(vd: Path, drafts: dict[str, str],
                  criteria: dict, reviewers: list[str]) -> str:
    """Run MIN_REVIEW_ROUNDS of review/revise. Returns final draft."""
    reviews_dir = vd / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    revisions_dir = vd / "revisions"
    revisions_dir.mkdir(parents=True, exist_ok=True)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _review_parallel(draft_text: str, criteria: dict, rnd: int,
                         prev: str, reviewers: list[str]) -> tuple[list[str], dict]:
        """Run all reviewers in parallel for one round. Returns (reviews, scores)."""
        def _do_review(rv: str) -> tuple[str, str, float]:
            style = MODELS.get(rv, {}).get("style", "")
            review = model_think(
                review_draft_prompt(draft_text, criteria, rnd, prev, style),
                model_name=rv, timeout=300,
            )
            score = 0.0
            if review:
                sm = re.search(r"OVERALL:\s*([\d.]+)", review)
                if sm:
                    score = float(sm.group(1))
            return rv, review or "", score

        reviews_list = []
        scores_dict = {}
        with ThreadPoolExecutor(max_workers=len(reviewers)) as pool:
            futures = [pool.submit(_do_review, rv) for rv in reviewers]
            for fut in as_completed(futures):
                rv, review, score = fut.result()
                if review:
                    reviews_list.append(f"**{rv}**:\n{review}")
                    scores_dict[rv] = score
        return reviews_list, scores_dict

    # --- Round 1: comparative review of all drafts (parallel per draft) ---
    if len(drafts) > 1:
        scores = {}
        all_reviews = {}

        for dname, dtxt in drafts.items():
            draft_reviews, draft_scores = _review_parallel(
                dtxt, criteria, 1, "", reviewers)
            all_reviews[dname] = "\n\n---\n\n".join(draft_reviews)
            scores[dname] = sum(draft_scores.values()) / max(len(draft_scores), 1)

        _save_review(reviews_dir, 1, scores, all_reviews)

        best = max(scores, key=scores.get)
        current_draft = drafts[best]
        prev_reviews = all_reviews[best]
        log.info("Round 1: best=%s (%.1f/10)", best, scores[best])
        start_round = 2
    else:
        current_draft = list(drafts.values())[0]
        prev_reviews = ""
        start_round = 1

    # --- Rounds 2-N: iterative review + revise ---
    for rnd in range(start_round, MIN_REVIEW_ROUNDS + 1):
        log.info("Review round %d/%d", rnd, MIN_REVIEW_ROUNDS)

        round_reviews, round_scores = _review_parallel(
            current_draft, criteria, rnd,
            prev_reviews[-2000:] if prev_reviews else "",
            reviewers,
        )

        combined = "\n\n---\n\n".join(round_reviews)
        _save_review(reviews_dir, rnd, round_scores, {"current": combined})

        avg = sum(round_scores.values()) / max(len(round_scores), 1)
        log.info("Round %d avg score: %.1f/10", rnd, avg)

        if avg >= 9.0 and rnd >= 3:
            log.info("Score >= 9.0 at round %d, stopping early", rnd)
            break

        # Revise (skip on last round)
        if rnd < MIN_REVIEW_ROUNDS:
            revised = model_think(
                revise_draft_prompt(current_draft, combined, criteria, rnd),
                model_name="claude", timeout=300,
            )
            if revised:
                current_draft = revised
                (revisions_dir / f"round_{rnd:02d}.md").write_text(
                    revised, encoding="utf-8",
                )
                prev_reviews = combined

    return current_draft


def _save_review(reviews_dir: Path, rnd: int, scores: dict, reviews: dict):
    """Persist review data for a round."""
    data = {
        "round": rnd,
        "scores": scores,
        "reviews": {k: v[:5000] for k, v in reviews.items()},
    }
    (reviews_dir / f"round_{rnd:02d}.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# CHECK for user responses to active writing projects
# ---------------------------------------------------------------------------

def check_writing_responses() -> list[dict]:
    """Check Apple Notes for plan approvals or draft feedback.

    Looks for notes with "计划:" or "初稿:" prefix that the user has changed
    from Status: wip to Status: done.

    Returns list of {workspace, project, content}.
    """
    active = find_active_projects()
    if not active:
        return []

    notes = fetch_notes(NOTES_INBOX_FOLDER)
    if not notes:
        return []

    responses = []
    for ws, proj in active:
        title = proj["title"]
        phase = proj["phase"]

        if phase == "await_plan":
            prefix = f"计划: {title}"
        elif phase == "await_feedback":
            prefix = f"初稿: {title}"
        else:
            continue

        for note in notes:
            if note["name"].strip() != prefix:
                continue

            body = note["body"]
            status = _parse_status(body)
            if status != "done":
                continue

            content = _extract_content(body, phase)
            if content:
                responses.append({
                    "workspace": ws,
                    "project": proj,
                    "content": content,
                })
                log.info("Found response for '%s' (%s)", title, phase)
            break

    return responses


def _parse_chapter_structure(outline: str) -> list[dict]:
    """Use Claude to quickly parse outline into chapter structure."""
    prompt = f"""分析以下大纲，提取章节结构。

大纲:
{outline}

输出JSON数组格式（不要markdown围栏，不要其他文字）:
[
  {{"number": 1, "title": "章节标题", "summary": "该章核心情节概述（80字以内）"}},
  ...
]

如果大纲没有明确的章节划分，请根据情节结构合理划分章节（通常8-15章）。
只输出JSON数组。"""

    result = claude_think(prompt, timeout=120, tier="heavy")
    if not result:
        return []

    try:
        m = re.search(r'\[.*\]', result, re.DOTALL)
        if m:
            return json.loads(m.group())
        return json.loads(result)
    except Exception as e:
        log.error("Failed to parse chapter structure: %s — %s", e, result[:200])
        return []


def _harsh_review_cycle(vd: Path, draft: str, outline: str,
                        criteria: dict, writer_models: list[str]) -> str:
    """Multiple rounds of harsh Claude review + GPT-5/DeepSeek revision."""
    reviews_dir = vd / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    revisions_dir = vd / "revisions"
    revisions_dir.mkdir(parents=True, exist_ok=True)

    current_draft = draft
    prev_reviews = ""

    for rnd in range(1, MIN_REVIEW_ROUNDS + 1):
        log.info("Harsh review round %d/%d", rnd, MIN_REVIEW_ROUNDS)

        # Claude reviews (harsh mode)
        review = claude_think(
            harsh_review_prompt(current_draft, criteria, rnd, outline, prev_reviews),
            timeout=300,
            tier="heavy",
        )

        if not review:
            log.warning("Review failed at round %d", rnd)
            continue

        # Parse score
        avg_score = 0.0
        sm = re.search(r"OVERALL:\s*([\d.]+)", review)
        if sm:
            avg_score = float(sm.group(1))

        # Save review
        review_data = {
            "round": rnd,
            "reviewer": "claude",
            "score": avg_score,
            "review": review[:10000],
        }
        (reviews_dir / f"round_{rnd:02d}.json").write_text(
            json.dumps(review_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log.info("Round %d score: %.1f/10", rnd, avg_score)

        # Early stop if quality is very high
        if avg_score >= 9.0 and rnd >= 3:
            log.info("Score >= 9.0 at round %d, stopping early", rnd)
            break

        # Revise using GPT-5/DeepSeek (skip last round)
        if rnd < MIN_REVIEW_ROUNDS:
            writer = random.choice(writer_models)
            log.info("Revising with %s based on round %d feedback", writer, rnd)

            revised = model_think(
                revise_draft_prompt(current_draft, review, criteria, rnd),
                model_name=writer, timeout=600,
            )

            if revised and len(revised) > len(current_draft) * 0.5:
                current_draft = revised
                (revisions_dir / f"round_{rnd:02d}.md").write_text(
                    revised, encoding="utf-8"
                )
                prev_reviews = review[-3000:]
                log.info("Revision by %s: %d chars", writer, len(revised))
            else:
                log.warning("Revision by %s too short or failed (got %d chars), keeping current",
                            writer, len(revised or ""))
                prev_reviews = review[-3000:]

    return current_draft


def start_from_plan(title: str, plan_path: str, writing_type: str = "novel"):
    """Start a writing project from an existing plan/outline file.

    Writers: GPT-5 and DeepSeek (alternate per chapter, linear sequential).
    Reviewers: Claude (harsh multi-round critique).
    Each chapter is written with all previous chapters in context.
    """
    plan_file = Path(plan_path)
    if not plan_file.exists():
        log.error("Plan file not found: %s", plan_path)
        return

    plan_text = plan_file.read_text(encoding="utf-8")

    if not title:
        title = plan_file.parent.name or plan_file.stem

    type_info = WRITING_CRITERIA.get(writing_type, WRITING_CRITERIA["novel"])
    criteria = type_info["criteria"]

    workspace = plan_file.parent
    workspace.mkdir(parents=True, exist_ok=True)

    vd = _vdir(workspace, 1)
    (vd / "plan_approved.md").write_text(plan_text, encoding="utf-8")
    chapters_dir = vd / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)

    project = {
        "title": title,
        "idea": f"(from plan file: {plan_path})",
        "type": writing_type,
        "type_name": type_info["name"],
        "phase": "writing",
        "version": 1,
        "criteria": criteria,
        "analysis": {"type": writing_type, "language": "zh", "complexity": "complex"},
        "created": datetime.now().isoformat(),
    }
    _save_project(workspace, project)

    log.info("Starting from plan: %s [%s] → %s", title, writing_type, workspace)

    # --- Step 1: Parse outline → chapter structure ---
    chapter_structure = _parse_chapter_structure(plan_text)
    if not chapter_structure:
        log.error("Failed to parse chapter structure for '%s'", title)
        project["phase"] = "error"
        _save_project(workspace, project)
        return

    total_chapters = len(chapter_structure)
    log.info("Outline parsed: %d chapters", total_chapters)

    # --- Step 2: Write chapters (GPT-5 + DeepSeek as writers) ---
    writer_pool = ["gpt5", "deepseek"]
    all_chapter_texts = []

    for i, ch_info in enumerate(chapter_structure, 1):
        writer = writer_pool[(i - 1) % len(writer_pool)]
        ch_title = ch_info.get("title", f"第{i}章")
        log.info("Chapter %d/%d [%s] → writer: %s", i, total_chapters, ch_title, writer)

        # Build context: outline + all previous chapters
        prev_text = "\n\n---\n\n".join(all_chapter_texts) if all_chapter_texts else ""

        prompt = chapter_write_prompt(
            plan_text, ch_info, i, total_chapters, prev_text
        )

        chapter_text = model_think(prompt, model_name=writer, timeout=600)

        # Fallback chain: other writer → Claude
        if not chapter_text:
            fallback = writer_pool[1] if writer == writer_pool[0] else writer_pool[0]
            log.warning("Writer %s failed for ch%d, trying %s", writer, i, fallback)
            chapter_text = model_think(prompt, model_name=fallback, timeout=600)

        if not chapter_text:
            log.warning("Both API writers failed for ch%d, falling back to Claude", i)
            chapter_text = claude_think(prompt, timeout=600, tier="heavy")

        if not chapter_text:
            log.error("All writers failed for chapter %d — skipping", i)
            continue

        # Save chapter
        ch_file = chapters_dir / f"ch_{i:02d}.md"
        ch_file.write_text(f"# {ch_title}\n\n{chapter_text}", encoding="utf-8")
        all_chapter_texts.append(f"# {ch_title}\n\n{chapter_text}")
        log.info("Chapter %d done: %d chars by %s", i, len(chapter_text), writer)

    if not all_chapter_texts:
        log.error("No chapters written for '%s'", title)
        project["phase"] = "error"
        _save_project(workspace, project)
        return

    # --- Step 3: Assemble draft ---
    draft_text = "\n\n---\n\n".join(all_chapter_texts)
    draft_file = vd / "draft.md"
    draft_file.write_text(draft_text, encoding="utf-8")
    log.info("Draft assembled: %d chars, %d/%d chapters",
             len(draft_text), len(all_chapter_texts), total_chapters)

    # --- Step 4: Harsh review cycle (Claude reviews, GPT-5/DeepSeek revises) ---
    project["phase"] = "reviewing"
    _save_project(workspace, project)

    final_draft = _harsh_review_cycle(
        vd, draft_text, plan_text, criteria, writer_pool
    )
    (vd / "converged.md").write_text(final_draft, encoding="utf-8")

    # --- Step 5: Post for feedback ---
    project["phase"] = "await_feedback"
    _save_project(workspace, project)

    writers_used = ", ".join(writer_pool)
    preview = final_draft[:8000]
    note_body = (
        f"Status: wip\n\n"
        f"# 初稿: {title}\n\n"
        f"版本 1 | {len(all_chapter_texts)}章 | 写手: {writers_used} | 评审: Claude (严格模式)\n\n"
        f"{preview}\n\n---\n\n"
        f"完整文稿: {vd}/draft.md\n\n"
        f"## 反馈\n"
        f"请在下方写反馈，完成后将 Status 改为 done。\n"
        f"写 \"完成\" 表示满意，可以定稿。"
    )
    create_note(NOTES_INBOX_FOLDER, f"初稿: {title}", note_body)

    append_memory(
        f"Writing '{title}': {len(all_chapter_texts)}-chapter draft "
        f"(GPT-5/DeepSeek writers + Claude harsh review) complete."
    )
    log.info("Draft posted for feedback: %s", title)


# ---------------------------------------------------------------------------
# FULL PIPELINE — end-to-end writing without Apple Notes approval
# ---------------------------------------------------------------------------

from config import WRITINGS_OUTPUT_DIR
_WRITINGS_ROOT = WRITINGS_OUTPUT_DIR


def run_full_pipeline(title: str, body: str) -> tuple[Path, str]:
    """Run the full writing pipeline end-to-end. Returns (workspace, final_text).

    Used by TalkBridge tasks — no Apple Notes interaction,
    plan is auto-approved, output saved under writings/projects/.
    """
    import re as _re

    # Create workspace under writings/projects/
    slug = _re.sub(r"[^\w\s\u4e00-\u9fff-]", "", title[:30]).strip()
    slug = _re.sub(r"[\s_]+", "-", slug).strip("-") or "untitled"
    ws = _WRITINGS_ROOT / slug
    ws.mkdir(parents=True, exist_ok=True)

    log.info("Full writing pipeline: '%s' → %s", title, ws)

    # --- Analyze ---
    analysis = _analyze(body)
    (ws / "analysis.json").write_text(
        json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    type_key = analysis.get("type", "essay")
    type_info = WRITING_CRITERIA.get(type_key, WRITING_CRITERIA["essay"])
    criteria = type_info["criteria"]

    # --- Plan (multi-agent) ---
    soul_ctx = format_soul(load_soul())
    vd = _vdir(ws, 1)
    plans_dir = vd / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    plan = _plan(soul_ctx, analysis, body, plans_dir)
    (plans_dir / "merged.md").write_text(plan, encoding="utf-8")

    # Auto-approve the plan
    (vd / "plan_approved.md").write_text(plan, encoding="utf-8")

    # Save project state
    project = {
        "title": title,
        "idea": body,
        "type": type_key,
        "type_name": type_info["name"],
        "phase": "writing",
        "version": 1,
        "criteria": criteria,
        "analysis": analysis,
        "created": datetime.now().isoformat(),
        "source": "talkbridge",
    }
    _save_project(ws, project)

    # --- Write (3+ agents) ---
    writers = WRITING_MODELS[:max(3, len(WRITING_MODELS))]
    drafts = _write_drafts(soul_ctx, plan, body, vd, writers)
    if not drafts:
        log.error("All writers failed for '%s'", title)
        project["phase"] = "error"
        _save_project(ws, project)
        return ws, ""

    # --- Review cycle ---
    project["phase"] = "reviewing"
    _save_project(ws, project)

    reviewers = REVIEW_MODELS[:max(3, len(REVIEW_MODELS))]
    final_draft = _review_cycle(vd, drafts, criteria, reviewers)
    (vd / "converged.md").write_text(final_draft, encoding="utf-8")

    # --- Finalize ---
    final_text = f"# {title}\n\n{final_draft}"
    (ws / "final.md").write_text(final_text, encoding="utf-8")

    project["phase"] = "done"
    _save_project(ws, project)

    append_memory(
        f"Writing '{title}' [{type_key}]: full pipeline complete → {ws}"
    )
    log.info("Full pipeline done: '%s' (%d chars)", title, len(final_text))

    return ws, final_text


def find_active_projects() -> list[tuple[Path, dict]]:
    """Find all writing projects awaiting user input."""
    if not WORKSPACE_DIR.exists():
        return []

    active = []
    for d in WORKSPACE_DIR.iterdir():
        if not d.is_dir():
            continue
        pf = d / "project.json"
        if not pf.exists():
            continue
        try:
            p = json.loads(pf.read_text(encoding="utf-8"))
            if p.get("phase") in ("await_plan", "await_feedback"):
                active.append((d, p))
        except Exception:
            continue
    return active


def _parse_status(body: str) -> str:
    """Extract Status value from note body."""
    for line in body.split("\n"):
        m = re.match(r"^\s*[Ss]tatus[:\uff1a]\s*(\w+)", line)
        if m:
            return m.group(1).strip().lower()
    return ""


def _extract_content(body: str, phase: str) -> str:
    """Extract user-edited content from a note body, stripping boilerplate."""
    # Remove Status line
    lines = [l for l in body.split("\n")
             if not re.match(r"^\s*[Ss]tatus[:\uff1a]", l)]
    text = "\n".join(lines)

    if phase == "await_plan":
        # Return the plan content (between headers and trailing instructions)
        text = re.sub(r"\n---\n\n请审阅.*$", "", text, flags=re.DOTALL)
        return text.strip()

    if phase == "await_feedback":
        # Extract only the feedback section
        m = re.search(r"## 反馈\s*\n(.+?)(?:\n---|\Z)", text, re.DOTALL)
        if m:
            fb = m.group(1).strip()
            # Remove boilerplate instruction lines (not DOTALL — per-line only)
            fb = re.sub(r"请在下方写反馈[^\n]*\n?", "", fb)
            fb = re.sub(r'写 "完成"[^\n]*\n?', "", fb)
            fb = re.sub(r"写 \"完成\"[^\n]*\n?", "", fb)
            return fb.strip()

    return text.strip()
