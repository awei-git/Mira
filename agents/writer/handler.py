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

from preflight import preflight_check, verify_artifact
from runtime_context import build_runtime_context
from sub_agent import claude_think
from writing_workflow import run_full_pipeline

log = logging.getLogger("writer_agent")

_QUICK_WRITE_SIGNALS = (
    "短文", "短一点", "简短", "quick", "tweet", "note", "caption",
    "100字", "200字", "300字", "brief", "一句", "一段",
)


def preflight(workspace: Path, task_id: str, content: str,
              sender: str, thread_id: str, **kwargs) -> tuple[bool, str]:
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


def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str, **kwargs) -> str | None:
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
    (workspace / "output.md").write_text(final_text, encoding="utf-8")

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

    verify = verify_artifact(
        "file",
        str(workspace / "output.md"),
        {"min_size": 20},
    )
    if not verify.verified:
        log.error("Writer artifact verification failed: %s", verify.summary())
        return None

    summary = (
        f"Writing project complete: {title}. "
        f"Project: {project_dir}."
    )
    (workspace / "summary.txt").write_text(summary, encoding="utf-8")
    (workspace / "project_path.txt").write_text(str(project_dir), encoding="utf-8")
    return summary
