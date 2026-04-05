#!/usr/bin/env python3
"""Writing automation pipeline.

Usage:
    writing_agent.py run               # Canonical writing_workflow scheduler shim
    writing_agent.py status            # Show legacy idea/project statuses
    writing_agent.py iterate <slug>    # Legacy manual step for idea files
    writing_agent.py new               # Show template for legacy idea files
"""

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Load writing-specific config by file path (avoid collision with agent/config.py)
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
MAX_STEPS_PER_RUN = _wcfg.MAX_STEPS_PER_RUN
PROJECTS_DIR = _wcfg.PROJECTS_DIR
TEMPLATES_DIR = _wcfg.TEMPLATES_DIR
TYPE_ALIASES = _wcfg.TYPE_ALIASES
TYPE_SCAFFOLD = _wcfg.TYPE_SCAFFOLD

critique_prompt = _wprompts.critique_prompt
draft_prompt = _wprompts.draft_prompt
feedback_draft_prompt = _wprompts.feedback_draft_prompt
revise_prompt = _wprompts.revise_prompt
scaffold_prompt = _wprompts.scaffold_prompt

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
        ],
    )


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

# ---------------------------------------------------------------------------
# Idea file parsing and updating
# ---------------------------------------------------------------------------

def parse_idea(idea_path: Path) -> dict:
    """Parse an idea markdown file into a dict."""
    text = idea_path.read_text(encoding="utf-8")

    result = {
        "path": idea_path,
        "slug": idea_path.stem,
        "raw": text,
    }

    # Parse metadata fields: - **key**: value
    # Use [ \t]* instead of \s* to avoid matching newlines
    for key in [
        "type", "language", "platform", "target_words", "deadline",
        "state", "project_dir", "created", "scaffolded",
        "round_1_draft", "round_1_critique", "round_1_revision",
        "feedback_detected",
        "round_2_draft", "round_2_critique", "round_2_revision",
        "current_round", "last_error",
        "idea_hash",
    ]:
        match = re.search(
            rf"^[ \t]*-[ \t]*\*\*{re.escape(key)}\*\*:[ \t]*(.*)$",
            text,
            re.MULTILINE,
        )
        if match:
            result[key] = match.group(1).strip()

    # Extract content above the auto-managed section
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
            log.warning("Field '%s' not found in %s — status update skipped", key, idea_path.name)
        text = new_text

    idea_path.write_text(text, encoding="utf-8")
    log.info("Updated %s: %s", idea_path.name, updates)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def idea_content_hash(idea: dict) -> str:
    """Short hash of the idea content, excluding the Feedback section.

    Used to detect when the user has edited the idea's theme/key points
    after the project was already scaffolded. Feedback edits should NOT
    trigger a restart — they trigger the feedback round instead.
    """
    content = idea.get("content_above", "")
    # Strip out the ## Feedback section so feedback edits don't change the hash
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
        return False  # Never hashed → first run, not a change
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
        "Claude unavailable for %s (%s) — falling back to %s with serialized context",
        cwd,
        reason,
        CLAUDE_FALLBACK_MODEL,
    )
    output = (model_think(
        augmented_prompt,
        model_name=CLAUDE_FALLBACK_MODEL,
        timeout=CLAUDE_TIMEOUT,
    ) or "").strip()
    if output:
        log.info(
            "Fallback model %s succeeded in %s, output %d chars",
            CLAUDE_FALLBACK_MODEL,
            cwd,
            len(output),
        )
        return True, output
    return False, f"{reason}; fallback model {CLAUDE_FALLBACK_MODEL} returned empty output"

# ---------------------------------------------------------------------------
# Claude CLI wrapper
# ---------------------------------------------------------------------------

def run_claude(prompt: str, cwd: Path) -> tuple[bool, str]:
    """Run `claude -p` with the given prompt in the given directory.

    Returns (success, output).
    """
    # Build a clean environment without CLAUDECODE to avoid nested-session block
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
            else:
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
        except Exception as e:
            last_error = str(e)
            log.error("Claude error (attempt %d): %s", attempt, e)

    return _run_fallback_model(
        prompt,
        cwd,
        last_error or f"Claude failed after {CLAUDE_MAX_RETRIES} attempts",
    )

# ---------------------------------------------------------------------------
# Output parsing helpers
# ---------------------------------------------------------------------------

def parse_scaffold_output(output: str) -> dict[str, str]:
    """Parse scaffold output that uses ===FILE:name=== markers.

    Returns dict mapping filename -> content.
    """
    files = {}
    parts = re.split(r"===FILE:(.+?)===\n?", output)
    # parts[0] = preamble (empty/junk), parts[1]=filename1, parts[2]=content1, ...
    for i in range(1, len(parts) - 1, 2):
        filename = parts[i].strip()
        content = parts[i + 1].strip()
        if content:
            files[filename] = content
    return files


def save_output(output: str, target_path: Path, label: str) -> bool:
    """Save Claude's stdout to a file. Returns True if saved."""
    if not output.strip():
        log.warning("Empty output for %s — nothing to save", label)
        return False
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(output, encoding="utf-8")
    log.info("Saved %s -> %s (%d chars)", label, target_path.name, len(output))
    return True

# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def resolve_type(raw_type: str) -> str:
    """Resolve a type string (possibly Chinese) to a canonical English type."""
    t = raw_type.lower().strip()
    return TYPE_ALIASES.get(t, t)


def _check_topic_overlap(idea: dict) -> str | None:
    """Check if a similar article has already been published.

    Returns a warning string if overlap detected, None if clear.
    """
    try:
        from soul_manager import catalog_list
        from sub_agent import claude_think
    except ImportError:
        return None

    published = [e for e in catalog_list()
                 if e.get("status") == "published" and e.get("type") == "article"]
    if not published:
        return None

    pub_titles = "\n".join(
        f"- {e.get('title','')} ({e.get('date','')[:10]}): {e.get('description','')[:100]}"
        for e in published[-20:]  # last 20 articles
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
    """Create project directory and fill templates.

    If is_restart=True, wipe the old project dir and re-scaffold from
    the updated idea content.
    """
    # Guard: check for overlap with already-published articles
    if not is_restart:
        overlap = _check_topic_overlap(idea)
        if overlap:
            log.warning("Topic overlap detected for '%s': %s", idea.get("slug",""), overlap)
            update_idea_status(idea["path"], {
                "state": "overlap_blocked",
                "last_error": f"Topic overlap: {overlap}",
            })
            return False

    writing_type = resolve_type(idea.get("type", "essay"))
    if writing_type not in TYPE_SCAFFOLD:
        log.error("Unknown type '%s' for %s", writing_type, idea["slug"])
        return False

    scaffold = TYPE_SCAFFOLD[writing_type]
    project_dir = PROJECTS_DIR / idea["slug"]

    if project_dir.exists() and not is_restart:
        log.info("Project dir already exists: %s", project_dir)
        update_idea_status(idea["path"], {
            "state": "scaffolded",
            "project_dir": str(project_dir),
            "scaffolded": now_str(),
            "current_round": "1",
            "idea_hash": idea_content_hash(idea),
        })
        return True

    if is_restart and project_dir.exists():
        log.info("Restart: clearing old project dir %s", project_dir)
        shutil.rmtree(project_dir)

    # Create directories
    project_dir.mkdir(parents=True)
    for d in scaffold["dirs"]:
        (project_dir / d).mkdir(exist_ok=True)

    # Copy templates
    for target_name, template_name in scaffold["templates"].items():
        src = TEMPLATES_DIR / template_name
        dst = project_dir / target_name
        if src.exists():
            shutil.copy2(src, dst)
            log.info("Copied %s -> %s", template_name, target_name)

    # Save idea content as reference
    (project_dir / "idea.md").write_text(idea["content_above"], encoding="utf-8")

    # Run Claude to fill in the templates
    prompt = scaffold_prompt(idea["content_above"], writing_type)
    success, output = run_claude(prompt, project_dir)

    content_hash = idea_content_hash(idea)

    if success and output:
        # Parse ===FILE:xxx=== markers from stdout
        files = parse_scaffold_output(output)
        if files:
            for filename, content in files.items():
                filepath = project_dir / filename
                filepath.write_text(content, encoding="utf-8")
                log.info("Wrote scaffold file: %s (%d chars)", filename, len(content))
        else:
            # Fallback: no markers found — save entire output as 规格.md
            log.warning("No ===FILE:=== markers in scaffold output, saving as 规格.md")
            (project_dir / "规格.md").write_text(output, encoding="utf-8")

        update_idea_status(idea["path"], {
            "state": "scaffolded",
            "project_dir": str(project_dir),
            "created": now_str(),
            "scaffolded": now_str(),
            "current_round": "1",
            "idea_hash": content_hash,
            # Clear old round timestamps on restart
            "round_1_draft": "",
            "round_1_critique": "",
            "round_1_revision": "",
            "feedback_detected": "",
            "round_2_draft": "",
            "round_2_critique": "",
            "round_2_revision": "",
            "last_error": "",
        })
        return True
    else:
        error_msg = f"scaffold failed: {output[:200]}"
        _log_writing_failure(idea["slug"], "scaffold", error_msg)
        update_idea_status(idea["path"], {
            "state": "error",
            "last_error": error_msg,
        })
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
            _log_writing_failure(idea["slug"], f"draft_r{round_num}", "draft: empty output from Claude")
            update_idea_status(idea["path"], {
                "state": "error",
                "last_error": "draft: empty output from Claude",
            })
            return False

        # -- Word count validation --
        draft_text = (project_dir / "drafts" / f"draft_r{round_num}.md").read_text(encoding="utf-8")
        cjk_chars = len(re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf]', draft_text))
        en_words = len(re.findall(r'[a-zA-Z]+', draft_text))
        total_words = cjk_chars + en_words

        spec_path = project_dir / "规格.md"
        target_words = 0
        if spec_path.exists():
            spec_text = spec_path.read_text(encoding="utf-8")
            wc_match = re.search(r'(?:字数|word.?count|目标字数)[：:]\s*(\d+)', spec_text, re.IGNORECASE)
            if wc_match:
                target_words = int(wc_match.group(1))

        if target_words > 0 and total_words < target_words * 0.5:
            log.warning("Draft r%d word count %d is less than 50%% of target %d for '%s'",
                        round_num, total_words, target_words, idea.get("title", ""))

        if total_words < 200:
            log.error("Draft r%d is only %d words - too short to be a real draft for '%s'",
                      round_num, total_words, idea.get("title", ""))
            _log_writing_failure(idea["slug"], f"draft_r{round_num}", f"Draft too short: {total_words} words")
            update_idea_status(idea["path"], {"state": "error", "last_error": f"Draft too short: {total_words} words"})
            return False

        state = "drafting" if round_num == 1 else "feedback_drafting"
        update_idea_status(idea["path"], {
            "state": state,
            f"round_{round_num}_draft": now_str(),
        })
        return True
    else:
        error_msg = f"draft r{round_num} failed: {output[:200]}"
        _log_writing_failure(idea["slug"], f"draft_r{round_num}", error_msg)
        update_idea_status(idea["path"], {
            "state": "error",
            "last_error": error_msg,
        })
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
            _log_writing_failure(idea["slug"], f"critique_r{round_num}", "critique: empty output from Claude")
            update_idea_status(idea["path"], {
                "state": "error",
                "last_error": "critique: empty output from Claude",
            })
            return False

        # -- Critique structure validation --
        critique_text = (project_dir / "drafts" / f"critique_r{round_num}.md").read_text(encoding="utf-8")
        has_priorities = bool(re.search(r'P[012]', critique_text))
        has_actionable = bool(re.search(r'(?:修改|改|删|加|补|替换|重写|调整|移除|fix|change|add|remove|rewrite)', critique_text))
        if not has_priorities and not has_actionable:
            log.warning("Critique r%d for '%s' has no P0/P1/P2 labels and no actionable feedback",
                        round_num, idea.get("title", ""))

        state = "critiquing" if round_num == 1 else "feedback_critiquing"
        update_idea_status(idea["path"], {
            "state": state,
            f"round_{round_num}_critique": now_str(),
        })
        return True
    else:
        error_msg = f"critique r{round_num} failed: {output[:200]}"
        _log_writing_failure(idea["slug"], f"critique_r{round_num}", error_msg)
        update_idea_status(idea["path"], {
            "state": "error",
            "last_error": error_msg,
        })
        return False


def step_revision(idea: dict, round_num: int) -> bool:
    """Generate a revision based on critique."""
    writing_type = resolve_type(idea.get("type", "essay"))
    project_dir = Path(idea.get("project_dir", ""))

    prompt = revise_prompt(writing_type, round_num)
    success, output = run_claude(prompt, project_dir)

    if success:
        # Separate article body from revision log
        if "===REVISION_LOG===" in output:
            body, rev_log = output.split("===REVISION_LOG===", 1)
            output = body.strip()
            rev_log_path = project_dir / "drafts" / f"revision_log_r{round_num}.md"
            rev_log_path.write_text(rev_log.strip(), encoding="utf-8")
        revision_path = project_dir / "drafts" / f"revision_r{round_num}.md"
        if not save_output(output, revision_path, f"revision_r{round_num}"):
            _log_writing_failure(idea["slug"], f"revision_r{round_num}", "revision: empty output from Claude")
            update_idea_status(idea["path"], {
                "state": "error",
                "last_error": "revision: empty output from Claude",
            })
            return False

        next_state = "awaiting_feedback" if round_num == 1 else "done"
        update_idea_status(idea["path"], {
            "state": next_state,
            f"round_{round_num}_revision": now_str(),
        })
        return True
    else:
        error_msg = f"revision r{round_num} failed: {output[:200]}"
        _log_writing_failure(idea["slug"], f"revision_r{round_num}", error_msg)
        update_idea_status(idea["path"], {
            "state": "error",
            "last_error": error_msg,
        })
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
            _log_writing_failure(idea["slug"], f"feedback_draft_r{round_num}", "feedback draft: empty output from Claude")
            update_idea_status(idea["path"], {
                "state": "error",
                "last_error": "feedback draft: empty output from Claude",
            })
            return False

        update_idea_status(idea["path"], {
            "state": "feedback_drafting",
            f"round_{round_num}_draft": now_str(),
        })
        return True
    else:
        error_msg = f"feedback draft r{round_num} failed: {output[:200]}"
        _log_writing_failure(idea["slug"], f"feedback_draft_r{round_num}", error_msg)
        update_idea_status(idea["path"], {
            "state": "error",
            "last_error": error_msg,
        })
        return False


def check_feedback(idea: dict) -> bool:
    """Check for feedback via two mechanisms:

    1. A ## Feedback section in the idea file (preferred — triggers WatchPaths)
    2. A feedback.md dropped in the project dir (legacy / manual)

    If feedback is found in the idea file, copy it to the project dir
    as feedback.md so the prompt can read it.
    """
    project_dir = Path(idea.get("project_dir", ""))
    feedback_path = project_dir / FEEDBACK_FILENAME

    # Check idea file for ## Feedback section
    raw = idea.get("raw", "")
    feedback_match = re.search(
        r"^## Feedback[ \t]*\n(.*?)(?=\n---|\n<!-- AUTO-MANAGED|\Z)",
        raw,
        re.DOTALL | re.MULTILINE,
    )
    if feedback_match:
        feedback_text = feedback_match.group(1).strip()
        # Ignore placeholder text and empty/separator-only content
        if feedback_text and not feedback_text.startswith("[") and feedback_text != "---":
            log.info("Feedback found in idea file for %s", idea["slug"])
            # Copy feedback to project dir for the prompt to read
            feedback_path.write_text(feedback_text, encoding="utf-8")
            update_idea_status(idea["path"], {
                "state": "feedback_detected",
                "feedback_detected": now_str(),
                "current_round": "2",
            })
            return True

    # Fallback: check for feedback.md in project dir
    if feedback_path.exists():
        log.info("Feedback file detected for %s", idea["slug"])
        update_idea_status(idea["path"], {
            "state": "feedback_detected",
            "feedback_detected": now_str(),
            "current_round": "2",
        })
        return True

    log.info("No feedback yet for %s — waiting", idea["slug"])
    return False

# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

def advance_idea(idea: dict) -> bool:
    """Advance one idea by one step. Returns True if progress was made."""
    state = idea.get("state", "new").strip()
    round_num = int(idea.get("current_round", "0") or "0")

    log.info("Processing %s: state=%s, round=%d", idea["slug"], state, round_num)

    # --- Handle restart: user manually set state to "restart" ---
    if state == "restart":
        log.info("Restart requested for %s", idea["slug"])
        return step_scaffold(idea, is_restart=True)

    # --- Detect idea content edits on in-progress projects ---
    # If the idea content changed since last scaffold and the project
    # hasn't finished yet, treat it as a restart.
    if state not in ("new", "done", "error", "restart") and idea_changed(idea):
        log.info(
            "Idea content changed for %s (hash mismatch), restarting",
            idea["slug"],
        )
        return step_scaffold(idea, is_restart=True)

    if state == "new":
        return step_scaffold(idea)

    elif state == "scaffolded":
        return step_draft(idea, round_num or 1)

    elif state == "drafting":
        return step_critique(idea, round_num or 1)

    elif state == "critiquing":
        return step_revision(idea, round_num or 1)

    elif state == "awaiting_feedback":
        return check_feedback(idea)

    elif state == "feedback_detected":
        return step_feedback_draft(idea, round_num)

    elif state == "feedback_drafting":
        return step_critique(idea, round_num)

    elif state == "feedback_critiquing":
        return step_revision(idea, round_num)

    elif state == "ready_to_publish":
        log.info("%s is waiting for publish approval, skipping", idea["slug"])
        return False

    elif state in ("done", "error"):
        log.info("%s is %s, skipping", idea["slug"], state)
        return False

    else:
        log.error("Unknown state '%s' for %s", state, idea["slug"])
        return False

# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_run():
    """Daily automated run. Process all ideas that need work."""
    log.info("=" * 60)
    log.info("Starting daily pipeline run")

    IDEAS_DIR.mkdir(exist_ok=True)
    PROJECTS_DIR.mkdir(exist_ok=True)

    # Sync Apple Notes → idea files (before processing)
    try:
        from notes_sync import sync_notes
        synced = sync_notes()
        if synced:
            log.info("Synced %d notes: %s", len(synced), synced)
    except Exception as e:
        log.error("Notes sync failed (continuing): %s", e)

    idea_files = sorted(
        f for f in IDEAS_DIR.glob("*.md") if not f.name.startswith("_")
    )

    if not idea_files:
        log.info("No idea files found")
        return

    for idea_path in idea_files:
        try:
            idea = parse_idea(idea_path)
            state = idea.get("state", "new").strip()

            if state in ("done", "error", "ready_to_publish"):
                continue

            # Advance up to MAX_STEPS_PER_RUN steps per idea
            for _ in range(MAX_STEPS_PER_RUN):
                idea = parse_idea(idea_path)  # Re-parse after each update
                state = idea.get("state", "").strip()
                if state in ("done", "error", "awaiting_feedback", "ready_to_publish"):
                    break
                if not advance_idea(idea):
                    break

        except Exception as e:
            log.error("Error processing %s: %s", idea_path.name, e, exc_info=True)
            _log_writing_failure(idea_path.stem, "cmd_run", str(e)[:500])
            try:
                update_idea_status(idea_path, {
                    "state": "error",
                    "last_error": str(e)[:200],
                })
            except Exception:
                pass

    log.info("Daily run complete")


def cmd_status():
    """Show canonical writing_workflow projects and any remaining legacy ideas."""
    canonical_projects = _iter_canonical_projects()
    if canonical_projects:
        print(f"\n{'Project':<35} {'Phase':<18} {'Version':<8} {'Updated'}")
        print("-" * 85)
        for project_dir, project in canonical_projects:
            phase = project.get("phase", "-")
            version = project.get("version", "-")
            updated = project.get("updated_at", "-")
            print(f"{project_dir.name:<35} {phase:<18} {version!s:<8} {updated}")
        print()
    else:
        print("No canonical writing_workflow projects found.")

    IDEAS_DIR.mkdir(exist_ok=True)
    idea_files = sorted(
        f for f in IDEAS_DIR.glob("*.md") if not f.name.startswith("_")
    )

    if not idea_files:
        return

    print("Legacy idea files still present:")
    for idea_path in idea_files:
        idea = parse_idea(idea_path)
        state = idea.get("state", "new")
        print(f"  {idea['slug']}: {state}")


def _cmd_iterate_legacy(slug: str):
    """Legacy idea-file iterator kept for backward compatibility."""
    idea_path = IDEAS_DIR / f"{slug}.md"

    if not idea_path.exists():
        for f in IDEAS_DIR.glob("*.md"):
            if f.name.startswith("_"):
                continue
            idea = parse_idea(f)
            if idea.get("project_dir", "").endswith(slug):
                idea_path = f
                break

    if not idea_path.exists():
        print(f"[legacy] No idea file found for '{slug}'")
        print(f"[legacy] Available: {[f.stem for f in IDEAS_DIR.glob('*.md') if not f.name.startswith('_')]}")
        return

    idea = parse_idea(idea_path)
    state = idea.get("state", "new")
    print(f"[legacy] Current state: {state}")

    if advance_idea(idea):
        idea = parse_idea(idea_path)
        print(f"[legacy] Advanced to: {idea.get('state', 'unknown')}")
    else:
        print("[legacy] No progress made (already done, error, or awaiting feedback)")


def cmd_iterate(slug: str):
    """Advance canonical projects when available; otherwise fall back to legacy ideas."""
    project_match = _find_canonical_project(slug)
    if project_match:
        project_dir, project = project_match
        phase = project.get("phase", "unknown")
        print(f"Canonical project {project_dir.name}: phase={phase}")
        if phase == "plan_ready":
            _, advance_project = _get_canonical_writing_ops()
            advance_project(project_dir)
            refreshed = {
                workspace: latest_project
                for workspace, latest_project in _iter_canonical_projects()
            }
            latest = refreshed.get(project_dir, project)
            print(f"Advanced to: {latest.get('phase', 'unknown')}")
        elif phase == "draft_ready":
            print("Canonical project is waiting for feedback; not advancing automatically.")
        else:
            print("Canonical project is not in an advanceable phase.")
        return

    print(f"No canonical project found for '{slug}', falling back to legacy idea files.")
    _cmd_iterate_legacy(slug)


def _cmd_new_legacy():
    """Show template for creating legacy idea files."""
    template_path = IDEAS_DIR / "_template.md"

    if template_path.exists():
        print(f"\nCopy the template to create a new idea:")
        print(f"  cp '{template_path}' '{IDEAS_DIR}/my-idea.md'")
        print(f"\nThen edit the file to fill in your idea.")
    else:
        print(f"Template not found at {template_path}")

    IDEAS_DIR.mkdir(exist_ok=True)
    existing = [f.stem for f in IDEAS_DIR.glob("*.md") if not f.name.startswith("_")]
    if existing:
        print(f"\nExisting ideas: {existing}")


def cmd_new():
    """Point users to the canonical workflow while keeping legacy guidance available."""
    print("Canonical writing does not use idea-file scaffolds anymore.")
    print("Use the main agent path or `core.py write-from-plan` for new canonical projects.")
    _cmd_new_legacy()

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def cmd_sync():
    """Sync Apple Notes for the legacy idea-file workflow only."""
    print("Apple Notes sync is legacy-only and not part of the canonical writing workflow.")
    from notes_sync import sync_notes
    synced = sync_notes()
    if synced:
        print(f"[legacy] Synced {len(synced)} notes: {synced}")
    else:
        print("[legacy] No changes from Apple Notes")


def _get_canonical_writing_ops():
    """Return the canonical writing workflow functions."""
    from writing_workflow import check_writing_responses, advance_project
    return check_writing_responses, advance_project


def _run_canonical_pipeline() -> int:
    """Scheduler shim: advance canonical writing_workflow projects only."""
    check_writing_responses, advance_project = _get_canonical_writing_ops()
    advanced = 0
    for resp in check_writing_responses():
        phase = resp["project"].get("phase", "")
        if phase == "plan_ready":
            advance_project(resp["workspace"])
            advanced += 1
    log.info("Canonical writing pipeline advanced %d project(s)", advanced)
    return advanced


def _get_canonical_autowrite_runner():
    """Return the canonical autonomous-writing entry point."""
    from workflows.writing import run_autowrite_pipeline
    return run_autowrite_pipeline


def _run_canonical_autowrite(title: str, writing_type: str, idea_content: str,
                             task_id: str = ""):
    """Compatibility shim for the old `writing_agent.py auto` command."""
    task_id = task_id or f"autowrite_{datetime.now().strftime('%Y-%m-%d')}"
    log.warning("writing_agent.py auto is deprecated; delegating to canonical autowrite")
    runner = _get_canonical_autowrite_runner()
    runner(task_id, title, writing_type, idea_content)


def _iter_canonical_projects():
    """Return canonical writing_workflow projects from the shared workspace."""
    from config import WORKSPACE_DIR

    projects = []
    for project_dir in sorted(WORKSPACE_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        project_file = project_dir / "project.json"
        if not project_file.exists():
            continue
        try:
            project = json.loads(project_file.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Skipping canonical project %s: %s", project_dir, exc)
            continue
        projects.append((project_dir, project))
    return projects


def _find_canonical_project(slug: str):
    """Find a canonical writing_workflow project by directory name or title."""
    for project_dir, project in _iter_canonical_projects():
        if project_dir.name == slug or project.get("title") == slug:
            return project_dir, project
    return None


def cmd_auto(title: str, writing_type: str, idea_content: str):
    """Create an idea file from args and run the full pipeline on it.

    Called by do_autowrite_check() in core.py for autonomous writing.
    """
    IDEAS_DIR.mkdir(exist_ok=True)
    PROJECTS_DIR.mkdir(exist_ok=True)

    # Generate slug from title
    slug = title.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = slug.strip("-")[:50] or "auto-essay"

    idea_path = IDEAS_DIR / f"{slug}.md"

    # Parse idea_content: first line is title, then thesis, then outline
    lines = idea_content.strip().split("\n")
    raw_title = lines[0] if lines else title
    thesis = ""
    outline_points = []
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        if not thesis:
            thesis = line
        else:
            outline_points.append(line)

    # Build key points from outline (which may be JSON list or plain text)
    key_points = ""
    if outline_points:
        for pt in outline_points:
            # Strip JSON artifacts
            pt = pt.strip("[]'\",")
            if pt:
                key_points += f"- {pt}\n"

    # Create idea file
    idea_md = f"""# {title}

- **type**: {writing_type}
- **language**: en
- **platform**: Substack
- **target_words**: 2000
- **deadline**:

## Theme

{thesis}

## Key Points

{key_points}
## Notes

Autonomous writing by Mira. Write with personal voice — this is from lived experience.

## Feedback



---
<!-- AUTO-MANAGED BELOW — DO NOT EDIT -->
## Status

- **state**: new
- **project_dir**:
- **created**:
- **scaffolded**:
- **round_1_draft**:
- **round_1_critique**:
- **round_1_revision**:
- **feedback_detected**:
- **round_2_draft**:
- **round_2_critique**:
- **round_2_revision**:
- **current_round**: 0
- **idea_hash**:
- **last_error**:
"""

    idea_path.write_text(idea_md, encoding="utf-8")
    log.info("Created idea file: %s", idea_path.name)

    # Run the pipeline on this idea (use higher limit — auto is a one-shot run)
    # For autonomous writing, don't stop at awaiting_feedback — push through to done
    idea = parse_idea(idea_path)
    for _ in range(15):
        idea = parse_idea(idea_path)
        state = idea.get("state", "").strip()
        if state in ("done", "error"):
            break
        if not advance_idea(idea):
            break

    final_idea = parse_idea(idea_path)
    final_state = final_idea.get("state", "unknown")
    log.info("Auto writing '%s' finished in state: %s", title, final_state)

    # HARD RULE (CLAUDE.md): never auto-publish without user approval.
    # When writing is done, save article to pending_publish.json and ask for confirmation.
    project_dir = final_idea.get("project_dir", "")
    approval_sent = False
    if final_state in ("done", "awaiting_feedback") and project_dir:
        log.info("Writing done for '%s', requesting user approval before publishing", title)
        try:
            shared_dir = str(Path(__file__).resolve().parent.parent / "shared")
            if shared_dir not in sys.path:
                sys.path.insert(0, shared_dir)
            proj_path = Path(project_dir)

            # Find the best draft to publish.
            # Priority: final.md > draft_r[2+] (substantial) > R*_revised.md
            #           > revision_r*.md (substantial) > R[0-9]*.md
            # NOTE: draft_r1.md is a stub (sign-off message), not an article.
            #       draft_r2.md and above ARE the real revised articles.
            #       revision_r*.md is ALSO a sign-off stub — never use it as article text.
            MIN_ARTICLE_BYTES = 3000  # stubs are <500 bytes; real articles are >>3000
            final_file = proj_path / "final" / "final.md"
            if not final_file.exists():
                drafts_dir = proj_path / "drafts"
                if drafts_dir.exists():
                    # Try R*_revised.md first (explicit revised outputs)
                    candidates = sorted(drafts_dir.glob("R*_revised.md"), reverse=True)
                    if not candidates:
                        # draft_r2.md+ are the actual revised articles (round 2+)
                        candidates = [
                            f for f in sorted(
                                drafts_dir.glob("draft_r*.md"), reverse=True
                            )
                            if f.stat().st_size >= MIN_ARTICLE_BYTES
                            and re.search(r'draft_r(\d+)\.md$', f.name)
                            and int(re.search(r'draft_r(\d+)\.md$', f.name).group(1)) >= 2
                        ]
                    if not candidates:
                        candidates = sorted(drafts_dir.glob("R[0-9]*.md"), reverse=True)
                    # revision_r*.md is a sign-off stub — skip unless nothing else found
                    # and it's substantial
                    if not candidates:
                        candidates = [
                            f for f in sorted(
                                drafts_dir.glob("revision_r*.md"), reverse=True
                            )
                            if f.stat().st_size >= MIN_ARTICLE_BYTES
                        ]
                    if candidates:
                        final_file = candidates[0]

            if not final_file.exists():
                log.error("No publishable draft found for project '%s'", proj_path)
                _log_writing_failure(proj_path.name, "final_selection",
                                     f"No draft file found matching any selection pattern in {proj_path}/drafts/")

            if final_file.exists():
                article_text = final_file.read_text(encoding="utf-8")

                # Content validation of selected draft
                _valid_draft = True
                if not article_text or len(article_text.strip()) < 500:
                    log.error("Selected draft '%s' is too short (%d chars)", final_file, len(article_text or ""))
                    _log_writing_failure(proj_path.name, "final_validation",
                                         f"Draft too short: {len(article_text or '')} chars")
                    _valid_draft = False
                elif not article_text.strip().startswith('#'):
                    log.warning("Selected draft '%s' doesn't start with a heading", final_file)

                if not _valid_draft:
                    raise ValueError(f"Selected draft too short: {len(article_text or '')} chars")

                # Extract title from the article's first heading (authoritative)
                pub_title = title
                heading_match = re.search(r'^#\s+(.+)$', article_text, re.MULTILINE)
                if heading_match:
                    extracted = heading_match.group(1).strip()
                    # Use extracted title only if it looks English
                    if extracted and all(ord(c) < 0x4E00 or ord(c) > 0x9FFF for c in extracted):
                        pub_title = extracted

                # Save to global pending_publish.json — task_worker picks this up on "approve"
                import json as _json
                from config import MIRA_ROOT
                pending = {
                    "pub_title": pub_title,
                    "article_path": str(final_file),
                    "project_dir": str(proj_path),
                    "created": datetime.now().isoformat(),
                    "source": "auto",
                }
                pending_file = MIRA_ROOT / ".pending_publish.json"
                pending_file.write_text(
                    _json.dumps(pending, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                log.info("Pending approval saved for '%s' at %s", pub_title, pending_file)

                # Notify user via bridge with full article text
                from mira import Mira as _BridgeMira
                _bridge = _BridgeMira()
                _today = datetime.now().strftime("%Y-%m-%d")
                _task_id = f"autowrite_{_today}"
                preview_text = article_text[:4000]
                if len(article_text) > 4000:
                    preview_text += f"\n\n[...文章还有 {len(article_text) - 4000} 字，已截断]"
                approval_msg = (
                    f"写好了！终稿如下，确认后发布。\n\n"
                    f"**{pub_title}**\n\n"
                    f"---\n\n"
                    f"{preview_text}\n\n"
                    f"---\n\n"
                    f"回复 approve 确认发布，reject 取消。"
                )
                _bridge.update_task_status(
                    _task_id, "needs-input",
                    agent_message=approval_msg,
                )
                approval_sent = True
                log.info("Approval request sent for '%s'", pub_title)
            else:
                log.warning("No publishable draft found for '%s'", title)
        except Exception as e:
            log.error("Approval setup failed for '%s': %s", title, e)

    # Bridge status update — only for non-approval states
    if not approval_sent:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
            from mira import Mira
            bridge = Mira()
            today = datetime.now().strftime("%Y-%m-%d")
            task_id = f"autowrite_{today}"
            if final_state in ("done", "awaiting_feedback", "ready_to_publish"):
                bridge.update_task_status(
                    task_id, "working",
                    agent_message=f"写完了，等待用户确认发布。项目在 {project_dir}",
                )
            elif final_state == "error":
                bridge.update_task_status(
                    task_id, "error",
                    agent_message=f"写作出错了: {final_idea.get('last_error', 'unknown')}",
                )
            else:
                bridge.update_task_status(
                    task_id, "working",
                    agent_message=f"写作进行中，当前状态: {final_state}",
                )
        except Exception as e:
            log.error("Failed to update bridge: %s", e)


USAGE = """Usage: writing_agent.py <command> [args]

Commands:
    run                 Canonical writing_workflow scheduler shim
    status              Show legacy idea/project statuses
    iterate <slug>      Legacy manual step for idea files
    sync                Sync Apple Notes → legacy idea files
    new                 Show template for legacy idea files
    auto                Canonical autowrite shim (deprecated command)
"""


def main():
    setup_logging()

    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(1)

    command = sys.argv[1]

    # Parse optional flags
    args = sys.argv[2:]
    flags = {}
    i = 0
    while i < len(args):
        if args[i].startswith("--") and i + 1 < len(args):
            flags[args[i][2:]] = args[i + 1]
            i += 2
        else:
            i += 1

    if command == "run":
        _run_canonical_pipeline()
    elif command == "status":
        cmd_status()
    elif command == "sync":
        cmd_sync()
    elif command == "new":
        cmd_new()
    elif command == "iterate":
        if len(sys.argv) < 3:
            print("Usage: writing_agent.py iterate <idea-slug>")
            sys.exit(1)
        cmd_iterate(sys.argv[2])
    elif command == "auto":
        title = flags.get("title", "Untitled")
        writing_type = flags.get("type", "essay")
        idea = flags.get("idea", "")
        task_id = flags.get("task-id", "")
        _run_canonical_autowrite(title, writing_type, idea, task_id=task_id)
    else:
        print(f"Unknown command: {command}")
        print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()
