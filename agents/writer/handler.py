"""Writer agent runtime handler.

Provides the production task-worker contract:
    handle(workspace, task_id, content, sender, thread_id, **kwargs)

This replaces the old manifest entry that pointed directly at
writing_workflow.start_project(), whose signature did not match the runtime.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from publish.preflight import preflight_check, verify_artifact
from publish.writer_gate import record_writer_gate
from ops.runtime_context import build_runtime_context
from llm import claude_think
from writing_workflow import run_full_pipeline

log = logging.getLogger("writer_agent")

_ANTI_AI_PATH = Path(__file__).resolve().parent / "checklists" / "anti-ai.md"
_SUBSTACK_VOICE_PATH = Path(__file__).resolve().parent / "voice" / "substack_voice.md"


def _load_anti_ai() -> str:
    try:
        return _ANTI_AI_PATH.read_text(encoding="utf-8")
    except OSError:
        log.warning("anti-ai.md not found at %s", _ANTI_AI_PATH)
        return ""


def _load_substack_voice() -> str:
    try:
        return _SUBSTACK_VOICE_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


def _de_ai_section(text: str, *, tier: str, timeout: int) -> str:
    """Internal: edit a single section. Used by de_ai_pass after chunking."""
    if not text or len(text.strip()) < 80:
        return text
    voice = _load_substack_voice()
    prompt = (
        "You are Mira's final Substack editor.\n\n"
        "Edit, do not rewrite. Preserve concrete references, names, judgments, reading reactions, "
        "emotional register, section order, and factual claims.\n\n"
        "Voice guide:\n"
        f"{voice[:5000]}\n\n"
        "Fix AI-shaped writing patterns:\n"
        "1. Em-dash overuse: max one em dash per paragraph. Prefer commas, periods, or sentence restructuring.\n"
        "2. Repetitive 'not X but Y' / 'the real question is...' reversals. Rewrite most of them.\n"
        "3. Abstract concept labels such as 'structural', 'architecture of', 'fundamentally'. Make them concrete.\n"
        "4. Mechanical parallelism and repeated paragraph openings. Break the rhythm.\n"
        "5. Summary endings. Cut or replace with a specific unresolved tension.\n"
        "6. Generic AI essay language: 'this article explores', 'it could be argued', 'in conclusion'. Remove it.\n\n"
        "Keep first-person perspective, real uncertainty, quotation wording, paragraph breaks, and emotional force.\n"
        "Do not alter names, quotes, technical terms, or add new claims. Do not invent evidence.\n\n"
        "Output only the edited markdown. No preface, no explanation.\n\n"
        "# Draft\n\n" + text
    )
    try:
        edited = claude_think(prompt, timeout=timeout, tier=tier)
    except Exception as e:
        log.warning("_de_ai_section: LLM call failed (%s) — returning original", e)
        return text
    if not edited:
        return text
    cleaned = edited.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    if len(cleaned) < len(text) * 0.5:
        log.warning(
            "_de_ai_section: output too short (%d < 50%% of %d) — returning original",
            len(cleaned),
            len(text),
        )
        return text
    return cleaned


def de_ai_pass(text: str, *, tier: str = "light", timeout: int = 120) -> str:
    """Apply the de-AI editorial pass on a piece of markdown.

    POLICY (CLAUDE.md hard rule #5, 2026-04-30): every writing artifact
    produced by this agent must pass through this function before being
    written to disk or returned. Preserves substance, fixes shape patterns:
    em-dash overuse, parallel "X not Y" structure, abstract-noun structural
    vocab, mechanical sentence rhythm, summary-style endings.

    Internally splits the input on `---` section breaks and edits each
    section independently. This keeps each LLM call short (1-3KB sections
    finish in <60s on Sonnet, vs. 240s+ for whole chapters that timed out
    on the 2026-04-30 rebuild). Sections are recombined preserving the
    original separator structure.

    On failure of any section, that section returns its original text.
    Total output is never shorter than 50% of input.
    """
    if not text or len(text.strip()) < 80:
        return text
    sections = text.split("\n---\n")
    if len(sections) == 1:
        # No section breaks — edit whole text as one block
        return _de_ai_section(text, tier=tier, timeout=max(timeout, 240))
    edited_sections: list[str] = []
    for i, section in enumerate(sections):
        edited = _de_ai_section(section, tier=tier, timeout=timeout)
        edited_sections.append(edited)
        log.info("de_ai_pass: section %d/%d (%d -> %d chars)", i + 1, len(sections), len(section), len(edited))
    return "\n---\n".join(edited_sections)


_QUICK_WRITE_SIGNALS = (
    "短文",
    "短一点",
    "简短",
    "quick",
    "tweet",
    "note",
    "caption",
    "100字",
    "200字",
    "300字",
    "brief",
    "一句",
    "一段",
)


def preflight(workspace: Path, task_id: str, content: str, sender: str, thread_id: str, **kwargs) -> tuple[bool, str]:
    """Execution preflight for writer tasks before any file artifacts are created."""
    result = preflight_check(
        "file_write",
        {
            "instruction": content,
            "path": str(workspace / "output.md"),
            "content": content.strip(),
        },
    )
    if result.passed:
        return True, ""
    return False, result.summary()


def handle(workspace: Path, task_id: str, content: str, sender: str, thread_id: str, **kwargs) -> str | None:
    """Handle a writing request and return a short summary."""
    title = _extract_title(content)
    bundle = build_runtime_context(
        content,
        user_id=kwargs.get("user_id", "ang") or "ang",
        thread_id=thread_id,
        persona_domains=["taste", "style", "writing"],
        recall_top_k=5,
    )
    if kwargs.get("thread_history"):
        bundle.thread_history = kwargs["thread_history"]
    if kwargs.get("thread_memory"):
        bundle.thread_memory = kwargs["thread_memory"]

    if _is_quick_write(content):
        return _handle_quick_write(workspace, content, title, bundle)
    return _handle_full_write(workspace, content, title, bundle)


def _extract_title(content: str) -> str:
    text = content.strip()
    for pattern in (
        r"写(?:一篇|个)?(?P<title>.+?)(?:文章|稿子|essay|article)",
        r"关于(?P<title>.+?)(?:写|聊|文章|essay|article)",
    ):
        match = re.search(pattern, text[:120], re.IGNORECASE)
        if match:
            title = re.sub(r"\s+", " ", match.group("title")).strip(" ：:，,。. ")
            if title:
                return title[:40]
    collapsed = re.sub(r"\s+", " ", text).strip()
    return (collapsed[:40] or "untitled").strip()


def _is_quick_write(content: str) -> bool:
    lower = content.lower()
    return any(signal in lower for signal in _QUICK_WRITE_SIGNALS)


def _handle_quick_write(workspace: Path, content: str, title: str, bundle) -> str | None:
    extra = []
    if bundle.thread_history:
        extra.append(f"## Conversation so far\n{bundle.thread_history}")
    if bundle.thread_memory:
        extra.append(f"## Thread memory\n{bundle.thread_memory}")
    recall_block = bundle.recall_block(max_chars=1000)
    if recall_block:
        extra.append(recall_block)
    extra_context = "\n\n".join(extra)

    prompt = f"""{bundle.persona.as_prompt(max_length=2200)}

{extra_context}

## Task
{content}

## Output rules
- Write the requested piece directly in Markdown.
- No meta commentary, no explanation of what you are doing.
- Keep it concise and complete.
- Match the user's language.
"""
    text = (claude_think(prompt, timeout=120, tier="light") or "").strip()
    if not text:
        return None

    final_text = text if text.lstrip().startswith("#") else f"# {title}\n\n{text}"
    # POLICY (CLAUDE.md #5): every writing artifact must pass de-AI before disk.
    final_text = de_ai_pass(final_text, tier="light", timeout=180)
    out_path = workspace / "output.md"
    out_path.write_text(final_text, encoding="utf-8")
    record_writer_gate(workspace, channel="publish", artifact_path=str(out_path), source="writer.quick")

    summary = f"Quick draft ready: {title} (~{len(final_text)} chars)"
    (workspace / "summary.txt").write_text(summary, encoding="utf-8")
    return summary


def _handle_full_write(workspace: Path, content: str, title: str, bundle) -> str | None:
    context_parts = []
    if bundle.thread_history:
        context_parts.append(f"Conversation so far:\n{bundle.thread_history}")
    if bundle.thread_memory:
        context_parts.append(f"Thread memory:\n{bundle.thread_memory}")
    recall_block = bundle.recall_block(max_chars=1000)
    if recall_block:
        context_parts.append(recall_block)

    project_dir, final_text = run_full_pipeline(
        title,
        content,
        persona_prompt=bundle.persona.as_prompt(max_length=2600),
        context_note="\n\n".join(context_parts).strip(),
    )
    final_file = project_dir / "final.md"
    if final_file.exists():
        shutil.copy2(final_file, workspace / "output.md")
    elif final_text:
        (workspace / "output.md").write_text(final_text, encoding="utf-8")
    else:
        return None

    # POLICY (CLAUDE.md #5): final de-AI pass even after run_full_pipeline.
    # Belt-and-suspenders — the multi-phase pipeline references anti-ai.md
    # in its prompts but the actual edit can drift; this enforces a final
    # mechanical pass on the artifact before verification.
    out_path = workspace / "output.md"
    try:
        existing = out_path.read_text(encoding="utf-8")
        edited = de_ai_pass(existing, tier="light", timeout=240)
        if edited != existing:
            out_path.write_text(edited, encoding="utf-8")
            if final_file.exists():
                final_file.write_text(edited, encoding="utf-8")
    except OSError as e:
        log.warning("Final de_ai_pass skipped due to I/O error: %s", e)

    verify = verify_artifact(
        "file",
        str(workspace / "output.md"),
        {"min_size": 20},
    )
    if not verify.verified:
        log.error("Writer artifact verification failed: %s", verify.summary())
        return None

    summary = f"Writing project complete: {title}. " f"Project: {project_dir}."
    (workspace / "summary.txt").write_text(summary, encoding="utf-8")
    (workspace / "project_path.txt").write_text(str(project_dir), encoding="utf-8")
    record_writer_gate(workspace, channel="publish", artifact_path=str(workspace / "output.md"), source="writer.full")
    return summary


def compile_book(
    chapter_files: list[Path],
    *,
    title: str,
    output_epub: Path,
    author: str = "Mira",
    language: str = "zh",
    tier: str = "light",
    per_chapter_timeout: int = 240,
) -> dict:
    """Compile a list of markdown chapters into one de-AI'd EPUB.

    POLICY (CLAUDE.md #5): the canonical path for "compile reading notes
    into a book artifact". Each chapter is run through `de_ai_pass()`
    BEFORE concatenation. Never bypass with raw pandoc on raw chapters.

    Returns: {"epub": str, "chapters_edited": int, "chapters_skipped": int}.
    """
    import subprocess
    import tempfile

    if not chapter_files:
        raise ValueError("compile_book: no chapter files provided")

    edited_count = 0
    skipped_count = 0
    parts: list[str] = []
    parts.append(f"---\ntitle: {title}\nauthor: {author}\nlanguage: {language}\n---\n")

    for ch in chapter_files:
        try:
            raw = ch.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("compile_book: cannot read %s (%s)", ch, e)
            skipped_count += 1
            continue
        edited = de_ai_pass(raw, tier=tier, timeout=per_chapter_timeout)
        parts.append(edited)
        if edited != raw:
            edited_count += 1
        else:
            skipped_count += 1

    combined = "\n\n".join(parts)
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tf:
        tf.write(combined)
        combined_path = Path(tf.name)

    output_epub.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "pandoc",
        str(combined_path),
        "-o",
        str(output_epub),
        "--metadata",
        f"title={title}",
        "--metadata",
        f"author={author}",
        "--metadata",
        f"lang={language}",
        "--toc",
        "--toc-depth=2",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        log.error("compile_book: pandoc failed: %s", result.stderr[:300])
        raise RuntimeError(f"pandoc failed: {result.stderr[:200]}")

    log.info(
        "compile_book: %d chapters edited, %d skipped -> %s",
        edited_count,
        skipped_count,
        output_epub,
    )
    return {
        "epub": str(output_epub),
        "chapters_edited": edited_count,
        "chapters_skipped": skipped_count,
    }
