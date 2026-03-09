#!/usr/bin/env python3
"""Writing automation pipeline.

Usage:
    writing_agent.py run               # Automated daily run (LaunchAgent)
    writing_agent.py status            # Show all project statuses
    writing_agent.py iterate <slug>    # Manually advance one idea by one step
    writing_agent.py new               # Show template for creating new ideas
"""

import hashlib
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
        replacement = rf"\g<1>{value}"
        text = re.sub(pattern, replacement, text, flags=re.MULTILINE)

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
                log.info(
                    "Claude succeeded (attempt %d), output %d chars",
                    attempt,
                    len(output),
                )
                return True, output
            else:
                log.warning(
                    "Claude failed (attempt %d, exit %d): %s",
                    attempt,
                    result.returncode,
                    result.stderr[:500],
                )
        except subprocess.TimeoutExpired:
            log.warning("Claude timed out (attempt %d) after %ds", attempt, CLAUDE_TIMEOUT)
        except Exception as e:
            log.error("Claude error (attempt %d): %s", attempt, e)

    return False, f"Failed after {CLAUDE_MAX_RETRIES} attempts"

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


def step_scaffold(idea: dict, is_restart: bool = False) -> bool:
    """Create project directory and fill templates.

    If is_restart=True, wipe the old project dir and re-scaffold from
    the updated idea content.
    """
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
        update_idea_status(idea["path"], {
            "state": "error",
            "last_error": f"scaffold failed: {output[:200]}",
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
            update_idea_status(idea["path"], {
                "state": "error",
                "last_error": "draft: empty output from Claude",
            })
            return False

        state = "drafting" if round_num == 1 else "feedback_drafting"
        update_idea_status(idea["path"], {
            "state": state,
            f"round_{round_num}_draft": now_str(),
        })
        return True
    else:
        update_idea_status(idea["path"], {
            "state": "error",
            "last_error": f"draft r{round_num} failed: {output[:200]}",
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
            update_idea_status(idea["path"], {
                "state": "error",
                "last_error": "critique: empty output from Claude",
            })
            return False

        state = "critiquing" if round_num == 1 else "feedback_critiquing"
        update_idea_status(idea["path"], {
            "state": state,
            f"round_{round_num}_critique": now_str(),
        })
        return True
    else:
        update_idea_status(idea["path"], {
            "state": "error",
            "last_error": f"critique r{round_num} failed: {output[:200]}",
        })
        return False


def step_revision(idea: dict, round_num: int) -> bool:
    """Generate a revision based on critique."""
    writing_type = resolve_type(idea.get("type", "essay"))
    project_dir = Path(idea.get("project_dir", ""))

    prompt = revise_prompt(writing_type, round_num)
    success, output = run_claude(prompt, project_dir)

    if success:
        revision_path = project_dir / "drafts" / f"revision_r{round_num}.md"
        if not save_output(output, revision_path, f"revision_r{round_num}"):
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
        update_idea_status(idea["path"], {
            "state": "error",
            "last_error": f"revision r{round_num} failed: {output[:200]}",
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
        update_idea_status(idea["path"], {
            "state": "error",
            "last_error": f"feedback draft r{round_num} failed: {output[:200]}",
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

            if state in ("done", "error"):
                continue

            # Advance up to MAX_STEPS_PER_RUN steps per idea
            for _ in range(MAX_STEPS_PER_RUN):
                idea = parse_idea(idea_path)  # Re-parse after each update
                state = idea.get("state", "").strip()
                if state in ("done", "error", "awaiting_feedback"):
                    break
                if not advance_idea(idea):
                    break

        except Exception as e:
            log.error("Error processing %s: %s", idea_path.name, e, exc_info=True)
            try:
                update_idea_status(idea_path, {
                    "state": "error",
                    "last_error": str(e)[:200],
                })
            except Exception:
                pass

    log.info("Daily run complete")


def cmd_status():
    """Show status of all ideas/projects."""
    IDEAS_DIR.mkdir(exist_ok=True)
    idea_files = sorted(
        f for f in IDEAS_DIR.glob("*.md") if not f.name.startswith("_")
    )

    if not idea_files:
        print("No idea files found in ideas/")
        return

    print(f"\n{'Idea':<35} {'State':<22} {'Round':<6} {'Last Update'}")
    print("-" * 85)

    for idea_path in idea_files:
        idea = parse_idea(idea_path)
        state = idea.get("state", "new")
        round_num = idea.get("current_round", "0")

        # Find most recent timestamp
        timestamps = []
        for key in [
            "scaffolded", "round_1_draft", "round_1_critique",
            "round_1_revision", "feedback_detected",
            "round_2_draft", "round_2_critique", "round_2_revision",
        ]:
            val = idea.get(key, "")
            if val:
                timestamps.append(val)

        last_update = max(timestamps) if timestamps else "-"
        print(f"{idea['slug']:<35} {state:<22} {round_num:<6} {last_update}")

    print()


def cmd_iterate(slug: str):
    """Manually advance one idea by one step."""
    idea_path = IDEAS_DIR / f"{slug}.md"

    if not idea_path.exists():
        # Try matching by project_dir
        for f in IDEAS_DIR.glob("*.md"):
            if f.name.startswith("_"):
                continue
            idea = parse_idea(f)
            if idea.get("project_dir", "").endswith(slug):
                idea_path = f
                break

    if not idea_path.exists():
        print(f"No idea file found for '{slug}'")
        print(f"Available: {[f.stem for f in IDEAS_DIR.glob('*.md') if not f.name.startswith('_')]}")
        return

    idea = parse_idea(idea_path)
    state = idea.get("state", "new")
    print(f"Current state: {state}")

    if advance_idea(idea):
        idea = parse_idea(idea_path)
        print(f"Advanced to: {idea.get('state', 'unknown')}")
    else:
        print("No progress made (already done, error, or awaiting feedback)")


def cmd_new():
    """Show template for creating new ideas."""
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

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def cmd_sync():
    """Manually sync Apple Notes → idea files."""
    from notes_sync import sync_notes
    synced = sync_notes()
    if synced:
        print(f"Synced {len(synced)} notes: {synced}")
    else:
        print("No changes from Apple Notes")


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

    # Auto-publish if writing completed successfully
    project_dir = final_idea.get("project_dir", "")
    if final_state in ("done", "awaiting_feedback") and project_dir:
        log.info("Auto-publishing '%s' to Substack", title)
        try:
            publisher_dir = str(Path(__file__).resolve().parent.parent / "publisher")
            if publisher_dir not in sys.path:
                sys.path.insert(0, publisher_dir)
            from substack import publish_to_substack
            proj_path = Path(project_dir)
            # Find the best draft to publish
            final_file = proj_path / "final" / "final.md"
            if not final_file.exists():
                # Fall back to latest draft
                drafts_dir = proj_path / "drafts"
                if drafts_dir.exists():
                    draft_files = sorted(drafts_dir.glob("draft_r*.md"), reverse=True)
                    if draft_files:
                        final_file = draft_files[0]
            if final_file.exists():
                article_text = final_file.read_text(encoding="utf-8")
                pub_result = publish_to_substack(
                    title=title,
                    subtitle="",
                    article_text=article_text,
                    workspace=proj_path,
                )
                log.info("Published '%s': %s", title, pub_result)
            else:
                log.warning("No publishable draft found for '%s'", title)
        except Exception as e:
            log.error("Auto-publish failed for '%s': %s", title, e)

    # Notify bridge so user sees the result
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
        from mira import Mira
        bridge = Mira()
        today = datetime.now().strftime("%Y-%m-%d")
        task_id = f"autowrite_{today}"
        if final_state in ("done", "awaiting_feedback"):
            bridge.update_task_status(
                task_id, "done",
                agent_message=f"写完并发布了！项目在 {project_dir}",
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
    run                 Automated daily run (processes all ideas)
    status              Show status of all ideas/projects
    iterate <slug>      Manually advance one idea by one step
    sync                Sync Apple Notes → idea files
    new                 Show how to create a new idea
    auto                Autonomous writing (called by core.py)
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
        cmd_run()
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
        cmd_auto(title, writing_type, idea)
    else:
        print(f"Unknown command: {command}")
        print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()
