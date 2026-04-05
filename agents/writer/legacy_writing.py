#!/usr/bin/env python3
"""Legacy idea-file writing pipeline kept for backward compatibility."""

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import importlib.util

_writing_dir = Path(__file__).resolve().parent


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_wcfg = _load_module("writing_config", _writing_dir / "writer_config.py")
_wprompts = _load_module("writing_prompts", _writing_dir / "writer_prompts.py")

_shared_dir = str(_writing_dir.parent / "shared")
if _shared_dir not in sys.path:
    sys.path.insert(0, _shared_dir)
_super_dir = str(_writing_dir.parent / "super")
if _super_dir not in sys.path:
    sys.path.insert(0, _super_dir)

from config import CLAUDE_FALLBACK_MODEL
from sub_agent import model_think

CLAUDE_BIN = _wcfg.CLAUDE_BIN
CLAUDE_MAX_RETRIES = _wcfg.CLAUDE_MAX_RETRIES
CLAUDE_TIMEOUT = _wcfg.CLAUDE_TIMEOUT
FEEDBACK_FILENAME = _wcfg.FEEDBACK_FILENAME
IDEAS_DIR = _wcfg.IDEAS_DIR
LOGS_DIR = _wcfg.LOGS_DIR
PROJECTS_DIR = _wcfg.PROJECTS_DIR
TEMPLATES_DIR = _wcfg.TEMPLATES_DIR
TYPE_ALIASES = _wcfg.TYPE_ALIASES
TYPE_SCAFFOLD = _wcfg.TYPE_SCAFFOLD

critique_prompt = _wprompts.critique_prompt
draft_prompt = _wprompts.draft_prompt
feedback_draft_prompt = _wprompts.feedback_draft_prompt
revise_prompt = _wprompts.revise_prompt
scaffold_prompt = _wprompts.scaffold_prompt

log = logging.getLogger("writing-pipeline")

_QUOTA_SIGNALS = (
    "rate limit",
    "quota",
    "usage limit",
    "too many requests",
    "credit",
    "overloaded",
    "capacity",
)
_FALLBACK_CONTEXT_LIMIT = 12000
_FALLBACK_TOTAL_LIMIT = 60000


def _log_writing_failure(slug: str, step: str, error_msg: str):
    """Record a writing pipeline failure for structured diagnosis."""
    try:
        import sys as _sys

        _shared = str(Path(__file__).resolve().parents[1] / "shared")
        if _shared not in _sys.path:
            _sys.path.insert(0, _shared)
        from failure_log import record_failure

        record_failure(
            pipeline="writing",
            step=step,
            slug=slug,
            error_type="writing_pipeline_error",
            error_message=error_msg[:500],
        )
    except Exception:
        pass


def parse_idea(idea_path: Path) -> dict:
    """Parse an idea markdown file into a dict."""
    text = idea_path.read_text(encoding="utf-8")

    result = {
        "path": idea_path,
        "slug": idea_path.stem,
        "raw": text,
    }

    for key in [
        "type",
        "language",
        "platform",
        "target_words",
        "deadline",
        "state",
        "project_dir",
        "created",
        "scaffolded",
        "round_1_draft",
        "round_1_critique",
        "round_1_revision",
        "feedback_detected",
        "round_2_draft",
        "round_2_critique",
        "round_2_revision",
        "current_round",
        "last_error",
        "idea_hash",
    ]:
        match = re.search(
            rf"^[ \t]*-[ \t]*\*\*{re.escape(key)}\*\*:[ \t]*(.*)$",
            text,
            re.MULTILINE,
        )
        if match:
            result[key] = match.group(1).strip()

    parts = text.split("<!-- AUTO-MANAGED BELOW")
    result["content_above"] = parts[0].strip() if parts else text.strip()

    return result


def update_idea_status(idea_path: Path, updates: dict):
    """Update status fields in the idea file."""
    text = idea_path.read_text(encoding="utf-8")

    for key, value in updates.items():
        pattern = rf"(^[ \t]*-[ \t]*\*\*{re.escape(key)}\*\*:[ \t]*)(.*)$"
        new_text = re.sub(pattern, rf"\g<1>{value}", text, flags=re.MULTILINE)
        if new_text == text:
            log.warning(
                "Field '%s' not found in %s - status update skipped",
                key,
                idea_path.name,
            )
        text = new_text

    idea_path.write_text(text, encoding="utf-8")
    log.info("Updated %s: %s", idea_path.name, updates)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def idea_content_hash(idea: dict) -> str:
    """Short hash of the idea content, excluding the Feedback section."""
    content = idea.get("content_above", "")
    content = re.sub(
        r"## Feedback\s*\n.*",
        "",
        content,
        flags=re.DOTALL,
    ).strip()
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


def idea_changed(idea: dict) -> bool:
    """Return True if the idea content changed since last scaffold."""
    saved_hash = idea.get("idea_hash", "")
    if not saved_hash:
        return False
    current_hash = idea_content_hash(idea)
    return saved_hash != current_hash


def _is_quota_error(stderr: str) -> bool:
    lower = (stderr or "").lower()
    return any(sig in lower for sig in _QUOTA_SIGNALS)


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n\n...[truncated {omitted} chars]..."


def _collect_fallback_context(cwd: Path) -> str:
    """Serialize the project's working set so API fallback can reason without tools."""
    candidates: list[Path] = [
        cwd / "idea.md",
        cwd / "规格.md",
        cwd / "大纲.md",
        cwd / "章节.md",
        cwd / "描述.md",
        cwd / "修改.md",
        cwd / "CLAUDE.md",
        cwd / FEEDBACK_FILENAME,
        _writing_dir / "frameworks" / "universal.md",
        _writing_dir / "frameworks" / "essay.md",
        _writing_dir / "frameworks" / "novel.md",
        _writing_dir / "frameworks" / "blog.md",
        _writing_dir / "checklists" / "anti-ai.md",
        _writing_dir / "checklists" / "self-edit.md",
        _writing_dir / "checklists" / "pre-submit.md",
    ]

    drafts_dir = cwd / "drafts"
    if drafts_dir.exists():
        draft_files = sorted(
            drafts_dir.glob("*.md"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        candidates.extend(draft_files[:6])

    blocks = []
    seen: set[Path] = set()
    total_chars = 0
    for path in candidates:
        if not path.exists():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            blocks.append(f"### FILE: {path}\n[read failed: {exc}]")
            continue
        truncated = _truncate_text(text, _FALLBACK_CONTEXT_LIMIT)
        block = f"### FILE: {path}\n{truncated}"
        if total_chars + len(block) > _FALLBACK_TOTAL_LIMIT and blocks:
            break
        blocks.append(block)
        total_chars += len(block)

    if not blocks:
        return "No project files were readable."
    return "\n\n".join(blocks)


def _run_fallback_model(prompt: str, cwd: Path, reason: str) -> tuple[bool, str]:
    context = _collect_fallback_context(cwd)
    augmented_prompt = f"""{prompt}

下面是当前项目工作区和参考文件的快照。当前模型无法直接读文件，所以你必须只基于这些文件内容完成同样任务，并严格遵守原 prompt 的输出格式要求。不要解释，不要描述你的推理过程，只输出最终结果。

## Workspace snapshot

{context}
"""
    log.warning(
        "Claude unavailable for %s (%s) - falling back to %s with serialized context",
        cwd,
        reason,
        CLAUDE_FALLBACK_MODEL,
    )
    output = (
        model_think(
            augmented_prompt,
            model_name=CLAUDE_FALLBACK_MODEL,
            timeout=CLAUDE_TIMEOUT,
        )
        or ""
    ).strip()
    if output:
        log.info(
            "Fallback model %s succeeded in %s, output %d chars",
            CLAUDE_FALLBACK_MODEL,
            cwd,
            len(output),
        )
        return True, output
    return (
        False,
        f"{reason}; fallback model {CLAUDE_FALLBACK_MODEL} returned empty output",
    )


def run_claude(prompt: str, cwd: Path) -> tuple[bool, str]:
    """Run `claude -p` with the given prompt in the given directory."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE")}
    env["PATH"] = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"

    last_error = ""
    for attempt in range(1, CLAUDE_MAX_RETRIES + 1):
        try:
            log.info("Running claude (attempt %d) in %s", attempt, cwd)
            result = subprocess.run(
                [CLAUDE_BIN, "-p", prompt],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=CLAUDE_TIMEOUT,
                env=env,
            )

            if result.returncode == 0:
                output = result.stdout.strip()
                if not output:
                    last_error = "Claude returned empty output"
                    log.warning("Claude returned empty output (attempt %d)", attempt)
                    break
                log.info(
                    "Claude succeeded (attempt %d), output %d chars",
                    attempt,
                    len(output),
                )
                return True, output
            last_error = result.stderr[:500] or f"Claude exited {result.returncode}"
            log.warning(
                "Claude failed (attempt %d, exit %d): %s",
                attempt,
                result.returncode,
                last_error,
            )
            if _is_quota_error(result.stderr):
                return _run_fallback_model(prompt, cwd, "Claude quota/rate-limit")
        except subprocess.TimeoutExpired:
            last_error = f"Claude timed out after {CLAUDE_TIMEOUT}s"
            log.warning("Claude timed out (attempt %d) after %ds", attempt, CLAUDE_TIMEOUT)
        except Exception as exc:
            last_error = str(exc)
            log.error("Claude error (attempt %d): %s", attempt, exc)

    return _run_fallback_model(
        prompt,
        cwd,
        last_error or f"Claude failed after {CLAUDE_MAX_RETRIES} attempts",
    )


def parse_scaffold_output(output: str) -> dict[str, str]:
    """Parse scaffold output that uses ===FILE:name=== markers."""
    files = {}
    parts = re.split(r"===FILE:(.+?)===\n?", output)
    for i in range(1, len(parts) - 1, 2):
        filename = parts[i].strip()
        content = parts[i + 1].strip()
        if content:
            files[filename] = content
    return files


def save_output(output: str, target_path: Path, label: str) -> bool:
    """Save Claude's stdout to a file."""
    if not output.strip():
        log.warning("Empty output for %s - nothing to save", label)
        return False
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(output, encoding="utf-8")
    log.info("Saved %s -> %s (%d chars)", label, target_path.name, len(output))
    return True


def resolve_type(raw_type: str) -> str:
    """Resolve a type string to a canonical English type."""
    t = raw_type.lower().strip()
    return TYPE_ALIASES.get(t, t)


def _check_topic_overlap(idea: dict) -> str | None:
    """Check if a similar article has already been published."""
    try:
        from soul_manager import catalog_list
        from sub_agent import claude_think
    except ImportError:
        return None

    published = [
        entry
        for entry in catalog_list()
        if entry.get("status") == "published" and entry.get("type") == "article"
    ]
    if not published:
        return None

    pub_titles = "\n".join(
        f"- {entry.get('title', '')} ({entry.get('date', '')[:10]}): {entry.get('description', '')[:100]}"
        for entry in published[-20:]
    )
    idea_text = idea.get("content_above", "")[:800]

    result = claude_think(
        f"""Compare this new article idea against the list of already-published articles.
Is there significant thematic overlap (same core thesis, same argument, same angle)?

NEW IDEA:
{idea_text}

PUBLISHED ARTICLES:
{pub_titles}

Reply with ONLY one of:
- "CLEAR" if the idea is sufficiently distinct
- "OVERLAP: <published title>" if there is significant overlap, naming which article""",
        timeout=20,
    )
    if result and "OVERLAP" in result.upper():
        return result.strip()
    return None


def step_scaffold(idea: dict, is_restart: bool = False) -> bool:
    """Create project directory and fill templates."""
    if not is_restart:
        overlap = _check_topic_overlap(idea)
        if overlap:
            log.warning("Topic overlap detected for '%s': %s", idea.get("slug", ""), overlap)
            update_idea_status(
                idea["path"],
                {
                    "state": "overlap_blocked",
                    "last_error": f"Topic overlap: {overlap}",
                },
            )
            return False

    writing_type = resolve_type(idea.get("type", "essay"))
    if writing_type not in TYPE_SCAFFOLD:
        log.error("Unknown type '%s' for %s", writing_type, idea["slug"])
        return False

    scaffold = TYPE_SCAFFOLD[writing_type]
    project_dir = PROJECTS_DIR / idea["slug"]

    if project_dir.exists() and not is_restart:
        log.info("Project dir already exists: %s", project_dir)
        update_idea_status(
            idea["path"],
            {
                "state": "scaffolded",
                "project_dir": str(project_dir),
                "scaffolded": now_str(),
                "current_round": "1",
                "idea_hash": idea_content_hash(idea),
            },
        )
        return True

    if is_restart and project_dir.exists():
        log.info("Restart: clearing old project dir %s", project_dir)
        shutil.rmtree(project_dir)

    project_dir.mkdir(parents=True)
    for directory in scaffold["dirs"]:
        (project_dir / directory).mkdir(exist_ok=True)

    for target_name, template_name in scaffold["templates"].items():
        src = TEMPLATES_DIR / template_name
        dst = project_dir / target_name
        if src.exists():
            shutil.copy2(src, dst)
            log.info("Copied %s -> %s", template_name, target_name)

    (project_dir / "idea.md").write_text(idea["content_above"], encoding="utf-8")

    prompt = scaffold_prompt(idea["content_above"], writing_type)
    success, output = run_claude(prompt, project_dir)

    content_hash = idea_content_hash(idea)

    if success and output:
        files = parse_scaffold_output(output)
        if files:
            for filename, content in files.items():
                filepath = project_dir / filename
                filepath.write_text(content, encoding="utf-8")
                log.info("Wrote scaffold file: %s (%d chars)", filename, len(content))
        else:
            log.warning("No ===FILE:=== markers in scaffold output, saving as 规格.md")
            (project_dir / "规格.md").write_text(output, encoding="utf-8")

        update_idea_status(
            idea["path"],
            {
                "state": "scaffolded",
                "project_dir": str(project_dir),
                "created": now_str(),
                "scaffolded": now_str(),
                "current_round": "1",
                "idea_hash": content_hash,
                "round_1_draft": "",
                "round_1_critique": "",
                "round_1_revision": "",
                "feedback_detected": "",
                "round_2_draft": "",
                "round_2_critique": "",
                "round_2_revision": "",
                "last_error": "",
            },
        )
        return True

    error_msg = f"scaffold failed: {output[:200]}"
    _log_writing_failure(idea["slug"], "scaffold", error_msg)
    update_idea_status(
        idea["path"],
        {
            "state": "error",
            "last_error": error_msg,
        },
    )
    return False


def step_draft(idea: dict, round_num: int) -> bool:
    """Generate a draft."""
    writing_type = resolve_type(idea.get("type", "essay"))
    project_dir = Path(idea.get("project_dir", ""))

    if not project_dir.exists():
        log.error("Project dir does not exist: %s", project_dir)
        return False

    prompt = draft_prompt(writing_type, round_num)
    success, output = run_claude(prompt, project_dir)

    if success:
        draft_path = project_dir / "drafts" / f"draft_r{round_num}.md"
        if not save_output(output, draft_path, f"draft_r{round_num}"):
            _log_writing_failure(
                idea["slug"],
                f"draft_r{round_num}",
                "draft: empty output from Claude",
            )
            update_idea_status(
                idea["path"],
                {
                    "state": "error",
                    "last_error": "draft: empty output from Claude",
                },
            )
            return False

        draft_text = draft_path.read_text(encoding="utf-8")
        cjk_chars = len(re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]", draft_text))
        en_words = len(re.findall(r"[a-zA-Z]+", draft_text))
        total_words = cjk_chars + en_words

        spec_path = project_dir / "规格.md"
        target_words = 0
        if spec_path.exists():
            spec_text = spec_path.read_text(encoding="utf-8")
            wc_match = re.search(r"(?:字数|word.?count|目标字数)[：:]\s*(\d+)", spec_text, re.IGNORECASE)
            if wc_match:
                target_words = int(wc_match.group(1))

        if target_words > 0 and total_words < target_words * 0.5:
            log.warning(
                "Draft r%d word count %d is less than 50%% of target %d for '%s'",
                round_num,
                total_words,
                target_words,
                idea.get("title", ""),
            )

        if total_words < 200:
            log.error(
                "Draft r%d is only %d words - too short to be a real draft for '%s'",
                round_num,
                total_words,
                idea.get("title", ""),
            )
            _log_writing_failure(
                idea["slug"],
                f"draft_r{round_num}",
                f"Draft too short: {total_words} words",
            )
            update_idea_status(
                idea["path"],
                {
                    "state": "error",
                    "last_error": f"Draft too short: {total_words} words",
                },
            )
            return False

        state = "drafting" if round_num == 1 else "feedback_drafting"
        update_idea_status(
            idea["path"],
            {
                "state": state,
                f"round_{round_num}_draft": now_str(),
            },
        )
        return True

    error_msg = f"draft r{round_num} failed: {output[:200]}"
    _log_writing_failure(idea["slug"], f"draft_r{round_num}", error_msg)
    update_idea_status(
        idea["path"],
        {
            "state": "error",
            "last_error": error_msg,
        },
    )
    return False


def step_critique(idea: dict, round_num: int) -> bool:
    """Generate a critique."""
    writing_type = resolve_type(idea.get("type", "essay"))
    project_dir = Path(idea.get("project_dir", ""))

    prompt = critique_prompt(writing_type, round_num)
    success, output = run_claude(prompt, project_dir)

    if success:
        critique_path = project_dir / "drafts" / f"critique_r{round_num}.md"
        if not save_output(output, critique_path, f"critique_r{round_num}"):
            _log_writing_failure(
                idea["slug"],
                f"critique_r{round_num}",
                "critique: empty output from Claude",
            )
            update_idea_status(
                idea["path"],
                {
                    "state": "error",
                    "last_error": "critique: empty output from Claude",
                },
            )
            return False

        critique_text = critique_path.read_text(encoding="utf-8")
        has_priorities = bool(re.search(r"P[012]", critique_text))
        has_actionable = bool(
            re.search(
                r"(?:修改|改|删|加|补|替换|重写|调整|移除|fix|change|add|remove|rewrite)",
                critique_text,
            )
        )
        if not has_priorities and not has_actionable:
            log.warning(
                "Critique r%d for '%s' has no P0/P1/P2 labels and no actionable feedback",
                round_num,
                idea.get("title", ""),
            )

        state = "critiquing" if round_num == 1 else "feedback_critiquing"
        update_idea_status(
            idea["path"],
            {
                "state": state,
                f"round_{round_num}_critique": now_str(),
            },
        )
        return True

    error_msg = f"critique r{round_num} failed: {output[:200]}"
    _log_writing_failure(idea["slug"], f"critique_r{round_num}", error_msg)
    update_idea_status(
        idea["path"],
        {
            "state": "error",
            "last_error": error_msg,
        },
    )
    return False


def step_revision(idea: dict, round_num: int) -> bool:
    """Generate a revision based on critique."""
    writing_type = resolve_type(idea.get("type", "essay"))
    project_dir = Path(idea.get("project_dir", ""))

    prompt = revise_prompt(writing_type, round_num)
    success, output = run_claude(prompt, project_dir)

    if success:
        if "===REVISION_LOG===" in output:
            body, rev_log = output.split("===REVISION_LOG===", 1)
            output = body.strip()
            rev_log_path = project_dir / "drafts" / f"revision_log_r{round_num}.md"
            rev_log_path.write_text(rev_log.strip(), encoding="utf-8")
        revision_path = project_dir / "drafts" / f"revision_r{round_num}.md"
        if not save_output(output, revision_path, f"revision_r{round_num}"):
            _log_writing_failure(
                idea["slug"],
                f"revision_r{round_num}",
                "revision: empty output from Claude",
            )
            update_idea_status(
                idea["path"],
                {
                    "state": "error",
                    "last_error": "revision: empty output from Claude",
                },
            )
            return False

        next_state = "awaiting_feedback" if round_num == 1 else "done"
        update_idea_status(
            idea["path"],
            {
                "state": next_state,
                f"round_{round_num}_revision": now_str(),
            },
        )
        return True

    error_msg = f"revision r{round_num} failed: {output[:200]}"
    _log_writing_failure(idea["slug"], f"revision_r{round_num}", error_msg)
    update_idea_status(
        idea["path"],
        {
            "state": "error",
            "last_error": error_msg,
        },
    )
    return False


def step_feedback_draft(idea: dict, round_num: int) -> bool:
    """Generate a draft incorporating user feedback."""
    writing_type = resolve_type(idea.get("type", "essay"))
    project_dir = Path(idea.get("project_dir", ""))

    prompt = feedback_draft_prompt(writing_type, round_num)
    success, output = run_claude(prompt, project_dir)

    if success:
        draft_path = project_dir / "drafts" / f"draft_r{round_num}.md"
        if not save_output(output, draft_path, f"feedback_draft_r{round_num}"):
            _log_writing_failure(
                idea["slug"],
                f"feedback_draft_r{round_num}",
                "feedback draft: empty output from Claude",
            )
            update_idea_status(
                idea["path"],
                {
                    "state": "error",
                    "last_error": "feedback draft: empty output from Claude",
                },
            )
            return False

        update_idea_status(
            idea["path"],
            {
                "state": "feedback_drafting",
                f"round_{round_num}_draft": now_str(),
            },
        )
        return True

    error_msg = f"feedback draft r{round_num} failed: {output[:200]}"
    _log_writing_failure(idea["slug"], f"feedback_draft_r{round_num}", error_msg)
    update_idea_status(
        idea["path"],
        {
            "state": "error",
            "last_error": error_msg,
        },
    )
    return False


def check_feedback(idea: dict) -> bool:
    """Check for feedback via the idea file or a legacy feedback.md."""
    project_dir = Path(idea.get("project_dir", ""))
    feedback_path = project_dir / FEEDBACK_FILENAME

    raw = idea.get("raw", "")
    feedback_match = re.search(
        r"^## Feedback[ \t]*\n(.*?)(?=\n---|\n<!-- AUTO-MANAGED|\Z)",
        raw,
        re.DOTALL | re.MULTILINE,
    )
    if feedback_match:
        feedback_text = feedback_match.group(1).strip()
        if feedback_text and not feedback_text.startswith("[") and feedback_text != "---":
            log.info("Feedback found in idea file for %s", idea["slug"])
            feedback_path.write_text(feedback_text, encoding="utf-8")
            update_idea_status(
                idea["path"],
                {
                    "state": "feedback_detected",
                    "feedback_detected": now_str(),
                    "current_round": "2",
                },
            )
            return True

    if feedback_path.exists():
        log.info("Feedback file detected for %s", idea["slug"])
        update_idea_status(
            idea["path"],
            {
                "state": "feedback_detected",
                "feedback_detected": now_str(),
                "current_round": "2",
            },
        )
        return True

    log.info("No feedback yet for %s - waiting", idea["slug"])
    return False


def advance_idea(idea: dict) -> bool:
    """Advance one idea by one step."""
    state = idea.get("state", "new").strip()
    round_num = int(idea.get("current_round", "0") or "0")

    log.info("Processing %s: state=%s, round=%d", idea["slug"], state, round_num)

    if state == "restart":
        log.info("Restart requested for %s", idea["slug"])
        return step_scaffold(idea, is_restart=True)

    if state not in ("new", "done", "error", "restart") and idea_changed(idea):
        log.info(
            "Idea content changed for %s (hash mismatch), restarting",
            idea["slug"],
        )
        return step_scaffold(idea, is_restart=True)

    if state == "new":
        return step_scaffold(idea)
    if state == "scaffolded":
        return step_draft(idea, round_num or 1)
    if state == "drafting":
        return step_critique(idea, round_num or 1)
    if state == "critiquing":
        return step_revision(idea, round_num or 1)
    if state == "awaiting_feedback":
        return check_feedback(idea)
    if state == "feedback_detected":
        return step_feedback_draft(idea, round_num)
    if state == "feedback_drafting":
        return step_critique(idea, round_num)
    if state == "feedback_critiquing":
        return step_revision(idea, round_num)
    if state == "ready_to_publish":
        log.info("%s is waiting for publish approval, skipping", idea["slug"])
        return False
    if state in ("done", "error"):
        log.info("%s is %s, skipping", idea["slug"], state)
        return False

    log.error("Unknown state '%s' for %s", state, idea["slug"])
    return False


__all__ = [
    "IDEAS_DIR",
    "advance_idea",
    "parse_idea",
]
