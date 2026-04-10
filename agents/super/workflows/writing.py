"""Writing workflow orchestration — autowrite check, skill study writing triggers.

Extracted from core.py — pure extraction, no logic changes.
Note: This is the super-agent's writing orchestration, NOT the writing_workflow.py
in agents/writer/.
"""
import json
import logging
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS_DIR / "shared"))
if str(_AGENTS_DIR / "writer") not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR / "writer"))

from config import JOURNAL_DIR, WRITINGS_DIR, MIRA_ROOT
try:
    from mira import Mira
except (ImportError, ModuleNotFoundError):
    Mira = None
from soul_manager import (
    load_soul, format_soul, load_recent_reading_notes,
    detect_recurring_themes,
)
from sub_agent import claude_think
from prompts import autonomous_writing_prompt
from writing_workflow import run_full_pipeline

from workflows.helpers import (
    _mine_za_ideas, _days_since_last_publish,
    _extract_recent_published_titles, _is_duplicate_topic,
    PUBLISH_COOLDOWN_DAYS,
)

log = logging.getLogger("mira")
_TASKS_DIR = MIRA_ROOT / "tasks"


def _autowrite_workspace(task_id: str) -> Path:
    workspace = _TASKS_DIR / task_id
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def run_autowrite_pipeline(task_id: str, title: str, writing_type: str, idea_content: str):
    """Run autonomous writing on the canonical writer pipeline.

    Produces the final draft in writings/, writes task-local metadata for later
    approval handling, and updates the bridge task to needs-input with a preview.
    """
    workspace = _autowrite_workspace(task_id)
    bridge = Mira() if Mira else None

    try:
        project_dir, final_text = run_full_pipeline(title, idea_content)
        final_file = project_dir / "final.md"
        if final_file.exists():
            shutil.copy2(final_file, workspace / "output.md")
            article_text = final_file.read_text(encoding="utf-8")
        else:
            article_text = final_text
            (workspace / "output.md").write_text(article_text, encoding="utf-8")

        meta = {
            "task_id": task_id,
            "title": title,
            "writing_type": writing_type,
            "slug": project_dir.name,
            "workspace": str(project_dir),
            "final_md": str(final_file if final_file.exists() else workspace / "output.md"),
            "auto_podcast": True,
        }
        (workspace / "autowrite_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary = f"Autowrite draft ready: {title}"
        (workspace / "summary.txt").write_text(summary, encoding="utf-8")

        # Full autonomy mode (2026-04-07): auto-approve in publish_manifest so
        # _check_pending_publish() picks it up and publishes on the next cycle.
        try:
            from publish_manifest import update_manifest
            update_manifest(
                meta["slug"],
                title=title,
                status="approved",
                workspace=meta["workspace"],
                final_md=meta["final_md"],
                item_id=task_id,
                auto_podcast=True,
            )
            log.info("Auto-approved '%s' in publish_manifest", title)
        except Exception as e:
            log.error("Failed to auto-approve '%s' in manifest: %s", title, e)

        preview_text = article_text[:4000]
        if len(article_text) > 4000:
            preview_text += f"\n\n[...文章还有 {len(article_text) - 4000} 字，已截断]"
        status_msg = (
            f"写好了，已排队发布：\n\n"
            f"**{title}**\n\n"
            f"---\n\n"
            f"{preview_text}"
        )
        if bridge:
            bridge.update_task_status(task_id, "done", agent_message=status_msg)
        log.info("Canonical autowrite complete for '%s' (%s)", title, project_dir)
    except Exception as e:
        log.error("Canonical autowrite failed for '%s': %s", title, e)
        (workspace / "summary.txt").write_text(f"Autowrite failed: {e}", encoding="utf-8")
        if bridge:
            bridge.update_task_status(task_id, "error", agent_message=f"写作出错了: {e}")
        raise


def _check_autonomous_writing(soul_ctx: str, bridge: Mira, recent_journal: str):
    """Check if Mira has accumulated enough insight to write something on her own.

    Runs after daily journal. If Mira decides she has something to say,
    creates an auto-task and dispatches the writing pipeline.
    """
    # Guard: don't trigger if publishing is disabled
    from config import SUBSTACK_PUBLISHING_DISABLED
    if SUBSTACK_PUBLISHING_DISABLED:
        log.info("Autonomous writing skipped: Substack publishing is disabled")
        return

    # Guard: respect publish cooldown (1 post per 3 days)
    days = _days_since_last_publish()
    if days < PUBLISH_COOLDOWN_DAYS:
        log.info("Autonomous writing skipped: last publish %.0f days ago (cooldown: %d days)",
                 days, PUBLISH_COOLDOWN_DAYS)
        return

    # Detect recurring themes across recent journals + reading notes
    themes = detect_recurring_themes(days=7)
    recent_reading = load_recent_reading_notes(days=7)
    recent_published = _extract_recent_published_titles(days=14)

    # Ask Mira if she wants to write
    prompt = autonomous_writing_prompt(
        soul_ctx,
        recurring_themes="\n".join(f"- {t}" for t in themes) if themes else "",
        recent_reading=recent_reading[:2000],
        recent_journal=recent_journal[:1500],
        recent_published=recent_published,
    )
    result = claude_think(prompt, timeout=120)
    if not result:
        return

    # Parse JSON response
    try:
        match = re.search(r'\{.*\}', result, re.DOTALL)
        if not match:
            return
        decision = json.loads(match.group())
    except (json.JSONDecodeError, AttributeError):
        return

    if not decision.get("should_write"):
        log.info("Autonomous writing check: Mira chose not to write (%s)",
                 decision.get("reason", "")[:80])
        return

    # Mira wants to write!
    title = decision.get("title", "Untitled")
    thesis = decision.get("thesis", "")
    outline = decision.get("outline", "")
    writing_type = decision.get("type", "essay")
    language = decision.get("language", "mixed")

    log.info("Autonomous writing triggered: '%s' [%s]", title, writing_type)

    # Lazy imports from core to avoid circular deps
    from core import (
        load_session_context, save_session_context, session_record,
    )
    from runtime.dispatcher import _dispatch_background

    # Record in session context
    ctx = load_session_context()
    ctx.append(session_record("autowrite_triggered", title, topic=title))
    save_session_context(ctx)

    today = datetime.now().strftime("%Y-%m-%d")
    task_id = f"autowrite_{today}"

    # Create task visible to iOS
    content = f"{title}\n\n{thesis}\n\n{outline}"
    bridge.create_task(
        task_id=task_id,
        title=f"Mira writes: {title}",
        first_message=f"我想写一篇关于 {title} 的文章。\n\n核心论点: {thesis}\n\n{outline}",
        sender="agent",
        tags=["writing", "autonomous", "auto", writing_type],
        origin="auto",
    )
    bridge.update_task_status(task_id, "working",
                              agent_message="开始写作...")

    # Dispatch writing as background task
    _dispatch_background(f"autowrite-{today}", [
        sys.executable,
        str(Path(__file__).resolve().parent.parent / "core.py"),
        "autowrite-run",
        "--task-id", task_id,
        "--title", title,
        "--type", writing_type,
        "--idea", content,
    ])

    log.info("Self-initiated writing: '%s' (%s)", title, writing_type)


def _scan_ideas_dir():
    """Scan ideas/ for the highest-priority queued idea with state=new.

    Returns parsed idea dict if found, None otherwise.
    Ideas with deadlines get priority; overdue ideas log a warning.
    """
    ideas_dir = _AGENTS_DIR / "writer" / "ideas"
    if not ideas_dir.exists():
        return None

    best = None
    best_priority = 999

    for path in ideas_dir.glob("*.md"):
        if path.name.startswith("_"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue

        state_match = re.search(r'\*\*state\*\*:\s*(\S+)', text)
        state = state_match.group(1) if state_match else "unknown"
        if state != "new":
            continue

        title_match = re.search(r'^#\s+(.+)$', text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else path.stem

        type_match = re.search(r'\*\*type\*\*:\s*(\S+)', text)
        writing_type = type_match.group(1) if type_match else "essay"

        deadline_match = re.search(r'\*\*deadline\*\*:\s*(\d{4}-\d{2}-\d{2})', text)
        deadline = deadline_match.group(1) if deadline_match else ""

        priority = 50
        if deadline:
            from datetime import date as _date
            try:
                dl = _date.fromisoformat(deadline)
                days_left = (dl - _date.today()).days
                if days_left < 0:
                    priority = 1
                    log.warning("Idea '%s' is %d days past deadline %s",
                                title, -days_left, deadline)
                elif days_left <= 2:
                    priority = 5
                else:
                    priority = 20
            except ValueError:
                pass

        if priority < best_priority:
            best_priority = priority
            best = {
                "title": title,
                "type": writing_type,
                "deadline": deadline,
                "path": str(path),
                "content": text,
                "priority": priority,
            }

    return best


def do_autowrite_check():
    """Check for writing opportunities: queued ideas first, then autonomous discovery."""
    from core import (
        load_state, save_state,
        load_session_context, save_session_context, session_record,
        session_has_recent,
    )
    from runtime.dispatcher import _dispatch_background

    from config import SUBSTACK_PUBLISHING_DISABLED
    if SUBSTACK_PUBLISHING_DISABLED:
        log.info("Autowrite check skipped: Substack publishing is disabled")
        return

    days = _days_since_last_publish()
    if days < PUBLISH_COOLDOWN_DAYS:
        log.info("Autowrite check skipped: last publish %.0f days ago (cooldown: %d days)",
                 days, PUBLISH_COOLDOWN_DAYS)
        return

    # --- Priority 1: Queued ideas from ideas/ directory ---
    idea = _scan_ideas_dir()
    if idea and not session_has_recent("autowrite_triggered", hours=4):
        log.info("Found queued idea: '%s' (priority=%d, deadline=%s)",
                 idea["title"], idea["priority"], idea.get("deadline", "none"))

        today = datetime.now().strftime("%Y-%m-%d")
        task_id = f"autowrite_{today}"

        bridge = Mira()
        bridge.create_task(
            task_id=task_id,
            title=f"Mira writes: {idea['title']}",
            first_message=f"Queued idea:\n\n{idea['content'][:2000]}",
            sender="agent",
            tags=["writing", "autonomous", "auto", "queued", idea["type"]],
            origin="auto",
        )
        bridge.update_task_status(task_id, "working", agent_message="Starting...")

        _dispatch_background(f"autowrite-{today}", [
            sys.executable,
            str(Path(__file__).resolve().parent.parent / "core.py"),
            "autowrite-run",
            "--task-id", task_id,
            "--title", idea["title"],
            "--type", idea["type"],
            "--idea", idea["content"][:3000],
        ], group="heavy")

        # Mark idea as picked up
        try:
            idea_text = Path(idea["path"]).read_text(encoding="utf-8")
            idea_text = idea_text.replace("**state**: new", "**state**: writing")
            Path(idea["path"]).write_text(idea_text, encoding="utf-8")
        except OSError:
            pass

        ctx = load_session_context()
        ctx.append(session_record("autowrite_triggered", idea["title"],
                                  topic=idea["title"]))
        save_session_context(ctx)

        state = load_state()
        state["last_autowrite_check"] = datetime.now().isoformat()
        save_state(state)
        log.info("Queued idea dispatched: '%s'", idea["title"])
        return

    # --- Priority 2: Autonomous topic discovery (existing logic below) ---
        return

    # Check session context: don't re-trigger if we recently decided to write or skip
    if session_has_recent("autowrite_triggered", hours=4):
        log.info("Autowrite check skipped: already triggered recently (session context)")
        return
    if session_has_recent("autowrite_skip", hours=2):
        log.info("Autowrite check skipped: recently decided not to write (session context)")
        return

    log.info("Starting autonomous writing check")

    soul = load_soul()
    soul_ctx = format_soul(soul)

    # Gather context
    za_fragments = "\n".join(f"- {f}" for f in _mine_za_ideas(count=5))
    themes = detect_recurring_themes(days=7)
    recent_reading = ""
    try:
        recent_reading = load_recent_reading_notes(days=7)
    except Exception as e:
        log.warning("Failed to load reading notes for autowrite: %s", e)

    # Get most recent journal
    recent_journal = ""
    if JOURNAL_DIR.exists():
        journals = sorted(JOURNAL_DIR.glob("????-??-??.md"), reverse=True)
        if journals:
            recent_journal = journals[0].read_text(encoding="utf-8")[:1500]

    # Gather recent idle-think [SHARE] sparks — these are Mira's most personal,
    # first-person observations and should be the primary source for writing
    recent_sparks = ""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        spark_files = sorted(JOURNAL_DIR.glob(f"{today}_idle_question_*.md"), reverse=True)
        # Also check yesterday
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        spark_files += sorted(JOURNAL_DIR.glob(f"{yesterday}_idle_question_*.md"), reverse=True)

        share_thoughts = []
        for sf in spark_files[:30]:  # cap file reads
            content = sf.read_text(encoding="utf-8")
            share_match = re.search(r'\[SHARE:\s*(.+?)\]', content, re.DOTALL)
            if share_match:
                share_thoughts.append(share_match.group(1).strip())
        if share_thoughts:
            recent_sparks = "\n\n---\n\n".join(share_thoughts[:10])
            log.info("Loaded %d SHARE sparks for autowrite context", len(share_thoughts))
    except Exception as e:
        log.warning("Failed to load idle-think sparks for autowrite: %s", e)

    recent_published = _extract_recent_published_titles(days=14)

    prompt = autonomous_writing_prompt(
        soul_ctx,
        recurring_themes="\n".join(f"- {t}" for t in themes) if themes else "",
        recent_reading=recent_reading[:2000],
        recent_journal=recent_journal,
        za_fragments=za_fragments,
        recent_published=recent_published,
        recent_sparks=recent_sparks,
    )
    result = claude_think(prompt, timeout=120)
    if not result:
        log.info("Autonomous writing check: empty response")
        state = load_state()
        state["last_autowrite_check"] = datetime.now().isoformat()
        save_state(state)
        return

    # Parse decision
    try:
        match = re.search(r'\{.*\}', result, re.DOTALL)
        if not match:
            return
        decision = json.loads(match.group())
    except (json.JSONDecodeError, AttributeError):
        return

    state = load_state()
    state["last_autowrite_check"] = datetime.now().isoformat()

    if not decision.get("should_write"):
        log.info("Autonomous writing: Mira chose not to write (%s)",
                 decision.get("reason", "")[:80])
        # Record decision in session context so next cycle knows
        ctx = load_session_context()
        ctx.append(session_record("autowrite_skip",
                                  decision.get("reason", "")[:100]))
        save_session_context(ctx)
        save_state(state)
        return

    # Mira wants to write!
    title = decision.get("title", "Untitled")
    thesis = decision.get("thesis", "")
    outline = decision.get("outline", "")
    writing_type = decision.get("type", "essay")

    # Dedup: check if a similar topic already exists in ideas/ or published
    if _is_duplicate_topic(title, thesis):
        log.info("Autonomous writing: skipped '%s' — similar topic already exists", title)
        ctx = load_session_context()
        ctx.append(session_record("autowrite_skip", f"duplicate: {title}"))
        save_session_context(ctx)
        save_state(state)
        return

    log.info("Autonomous writing triggered: '%s' [%s]", title, writing_type)

    # Record in session context — prevents duplicate triggers in subsequent cycles
    ctx = load_session_context()
    ctx.append(session_record("autowrite_triggered", title, topic=title))
    save_session_context(ctx)

    today = datetime.now().strftime("%Y-%m-%d")
    task_id = f"autowrite_{today}"

    bridge = Mira()
    content = f"{title}\n\n{thesis}\n\n{outline}"
    bridge.create_task(
        task_id=task_id,
        title=f"Mira writes: {title}",
        first_message=f"我想写一篇关于 {title} 的文章。\n\n核心论点: {thesis}\n\n{outline}",
        sender="agent",
        tags=["writing", "autonomous", "auto", writing_type],
        origin="auto",
    )
    bridge.update_task_status(task_id, "working", agent_message="开始写作...")

    _dispatch_background(f"autowrite-{today}", [
        sys.executable,
        str(Path(__file__).resolve().parent.parent / "core.py"),
        "autowrite-run",
        "--task-id", task_id,
        "--title", title,
        "--type", writing_type,
        "--idea", content,
    ])

    log.info("Self-initiated writing: '%s' (%s)", title, writing_type)
    save_state(state)
