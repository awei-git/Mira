"""Manage the agent's soul: identity, memory, interests, skills.

Sub-modules:
  soul_io     — File I/O, integrity, backup
  soul_skills — Skills management, auditing, syncing
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from config import (
    IDENTITY_FILE,
    MEMORY_FILE,
    INTERESTS_FILE,
    WORLDVIEW_FILE,
    READING_NOTES_DIR,
    SKILLS_DIR,
    SKILLS_INDEX,
    SKILLS_FILE,
    MAX_MEMORY_LINES,
    MIRA_ROOT,
    CONVERSATIONS_DIR,
    EPISODES_DIR,
    CATALOG_FILE,
    CHANGELOG_FILE,
    CHANGELOG_ARCHIVE_DIR,
    CHANGELOG_MAX_LINES,
    LOGS_DIR,
)
from user_paths import user_journal_dir, user_reading_notes_dir

# ---------------------------------------------------------------------------
# Re-export from sub-modules for backward compatibility
# ---------------------------------------------------------------------------
from memory.soul_io import (  # noqa: F401
    _atomic_write,
    _locked_write,
    _locked_read_modify_write,
    _log_change,
    _compute_hash,
    _save_hashes,
    _load_hashes,
    _rotate_backup,
    verify_soul_integrity,
    _protected_write,
)
from memory.soul_skills import (  # noqa: F401
    SkillAuditFailedError,
    _load_skill_audit_hashes,
    _save_skill_audit_hash,
    load_skills_summary,
    load_skills_for_task,
    load_skill,
    check_prompt_injection,
    _check_declaration_behavior_consistency,
    _levenshtein,
    audit_skill,
    _classify_skill_type,
    save_skill,
    update_skill,
    get_stale_skills,
    _refresh_audited_at,
    quarantine_skill,
    rebuild_skills_md,
    _sync_skills_to_claude_md,
    _load_all_skill_indexes,
    resolve_skill_audit_failure,
)

log = logging.getLogger("mira")


def load_soul() -> dict:
    """Load the full soul context. Verifies integrity of protected files."""
    violations = verify_soul_integrity()
    if violations:
        log.critical("Soul integrity check failed: %s", violations)

    return {
        "identity": _read_or_default(IDENTITY_FILE, "No identity defined yet."),
        "memory": _read_or_default(MEMORY_FILE, "No memories yet."),
        "interests": _read_or_default(INTERESTS_FILE, "No interests defined yet."),
        "worldview": _read_or_default(WORLDVIEW_FILE, "No worldview yet."),
        "skills": load_skills_summary(),
    }


def format_soul(soul: dict) -> str:
    """Format the full soul as a string for injection into prompts."""
    parts = [
        "# My Identity\n",
        soul["identity"],
        "\n\n# My Worldview\n",
        soul["worldview"],
        "\n\n# My Memory\n",
        soul["memory"],
        "\n\n# My Current Interests\n",
        soul["interests"],
    ]
    if soul["skills"]:
        parts.append("\n\n# My Skills\n")
        parts.append(soul["skills"])

    # Self-evaluation scorecard (if available)
    try:
        from evaluation.reporting import format_scorecard

        card = format_scorecard()
        if card:
            parts.append("\n\n# My Self-Evaluation Scores\n")
            parts.append(card)
    except (ImportError, ModuleNotFoundError) as e:
        log.debug("Scorecard loading skipped: %s", e)

    # Active improvement plan (from score → action pipeline)
    try:
        from evaluation.improvement import get_active_improvements

        improvements = get_active_improvements()
        if improvements:
            parts.append("\n\n# Active Self-Improvement Focus\n")
            parts.append(improvements)
    except (ImportError, ModuleNotFoundError):
        pass

    return "".join(parts)


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


def append_memory(entry: str, user_id: str = "ang"):
    """Append a timestamped entry to memory. Enforces MAX_MEMORY_LINES.

    Overflowed lines (trimmed from memory.md) persist in PostgreSQL
    episodic_memory as 'memory_overflow' and remain searchable via vector search.
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"- [{ts}] {entry}\n"
    overflow_lines = []

    def _modify(text):
        nonlocal overflow_lines
        if not text:
            text = "# Memory\n\n"
        text += line
        lines = text.split("\n")
        if len(lines) > MAX_MEMORY_LINES:
            header = lines[:2]
            entries = lines[2:]
            overflow_lines = entries[: -(MAX_MEMORY_LINES - 2)]
            trimmed = entries[-(MAX_MEMORY_LINES - 2) :]
            text = "\n".join(header + trimmed)
            log.info("Memory trimmed to %d lines", MAX_MEMORY_LINES)
        return text

    _locked_read_modify_write(MEMORY_FILE, _modify)
    log.info("Memory +: %s", entry[:80])
    _log_change("APPEND_MEMORY", "memory.md", entry[:80])

    # Persist to Postgres (non-blocking best-effort)
    try:
        from memory.store import get_store

        store = get_store()
        store.remember(entry, source_type="memory_entry", importance=0.5, user_id=user_id)
        # Persist overflowed lines so they remain searchable
        if overflow_lines:
            overflow_text = "\n".join(overflow_lines)
            store.remember(overflow_text, source_type="memory_overflow", importance=0.3, user_id=user_id)
    except (ImportError, ModuleNotFoundError, ConnectionError, OSError) as e:
        log.debug("Postgres memory persist skipped: %s", e)


def update_memory(new_content: str):
    """Replace memory file with new content (used by reflect mode)."""
    _locked_write(MEMORY_FILE, new_content)
    log.info("Memory updated (%d lines)", new_content.count("\n"))
    _log_change("UPDATE_MEMORY", "memory.md", f"{new_content.count(chr(10))} lines")


def get_memory_size() -> int:
    """Return line count of memory file."""
    if not MEMORY_FILE.exists():
        return 0
    return MEMORY_FILE.read_text(encoding="utf-8").count("\n")


# ---------------------------------------------------------------------------
# Interests
# ---------------------------------------------------------------------------


def update_interests(new_content: str):
    """Replace interests file."""
    _locked_write(INTERESTS_FILE, new_content)
    log.info("Interests updated")
    _log_change("UPDATE_INTERESTS", "interests.md")


# ---------------------------------------------------------------------------
# Worldview
# ---------------------------------------------------------------------------


def update_worldview(new_content: str):
    """Replace worldview file (used by reflect). Integrity-protected."""
    _protected_write(WORLDVIEW_FILE, new_content)
    log.info("Worldview updated (%d lines)", new_content.count("\n"))
    _log_change("UPDATE_WORLDVIEW", "worldview.md", f"{new_content.count(chr(10))} lines")


# ---------------------------------------------------------------------------
# Reading Notes
# ---------------------------------------------------------------------------


def save_reading_note(title: str, reflection: str, user_id: str = "ang"):
    """Save a personal reading reflection after deep dive."""
    notes_dir = user_reading_notes_dir(user_id)
    notes_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    slug = title.lower().replace(" ", "-")[:40]
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    path = notes_dir / f"{today}_{slug}.md"
    _atomic_write(path, f"# Reading Note: {title}\n\n*{today}*\n\n{reflection}")
    log.info("Reading note saved: %s", path.name)
    _log_change("SAVE_READING_NOTE", path.name, title)
    return path


def save_knowledge_note(title: str, content: str, source_task_id: str = "", user_id: str = "ang") -> Path | None:
    """Save a knowledge write-back note with provenance.

    Thin wrapper around save_reading_note that adds source tracing,
    and persists to PostgreSQL with higher importance for recall.
    """
    provenance = f"*Source: task {source_task_id}*\n\n" if source_task_id else ""
    path = save_reading_note(title, f"{provenance}{content}", user_id=user_id)
    if not path:
        return None
    _log_change("KNOWLEDGE_WRITEBACK", path.name, title[:60])
    # Persist to Postgres with elevated importance
    try:
        from memory.store import get_store

        store = get_store()
        store.remember(
            f"{title}\n\n{content}",
            source_type="writeback",
            source_id=source_task_id,
            importance=0.7,
            user_id=user_id,
        )
    except (ImportError, ModuleNotFoundError, ConnectionError, OSError) as e:
        log.debug("Postgres writeback persist skipped: %s", e)
    # Auto-link to related knowledge
    try:
        from knowledge_links import auto_link

        auto_link(f"{title}\n\n{content}", "reading_note", path.name, user_id=user_id)
    except Exception as e:
        log.debug("Auto-link skipped: %s", e)
    return path


def load_recent_reading_notes(days: int = 14, user_id: str = "ang") -> str:
    """Load recent reading notes for use in reflect/journal."""
    notes_dir = user_reading_notes_dir(user_id)
    if not notes_dir.exists():
        return ""
    from datetime import timedelta

    cutoff = datetime.now() - timedelta(days=days)
    texts = []
    for path in sorted(notes_dir.glob("*.md")):
        try:
            date_str = path.stem[:10]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date >= cutoff:
                content = path.read_text(encoding="utf-8")
                texts.append(content[:1500])
        except ValueError:
            continue
    return "\n\n---\n\n".join(texts) if texts else ""


def detect_recurring_themes(days: int = 7) -> list[str]:
    """Scan recent journals + reading notes for recurring themes.

    Returns a list of theme strings that appear in 3+ entries.
    Simple keyword frequency approach — good enough to seed autonomous writing.
    """
    from collections import Counter

    texts = []
    # Gather journal entries
    journal_dir = user_journal_dir("ang")
    if journal_dir.exists():
        from datetime import timedelta

        cutoff = datetime.now() - timedelta(days=days)
        for path in sorted(journal_dir.glob("*.md")):
            try:
                date_str = path.stem[:10]
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
                if file_date >= cutoff:
                    texts.append(path.read_text(encoding="utf-8"))
            except ValueError:
                continue

    # Gather reading notes
    notes = load_recent_reading_notes(days=days)
    if notes:
        texts.append(notes)

    if not texts:
        return []

    # Extract significant phrases (simple: lines that start with "-" or contain key patterns)
    combined = "\n".join(texts)
    # Look for concepts mentioned multiple times across entries
    # Extract capitalized concepts, quoted terms, and bold terms
    concepts = re.findall(r"\*\*(.+?)\*\*", combined)
    concepts += re.findall(r'"(.+?)"', combined)
    concepts += re.findall(r"「(.+?)」", combined)

    # Count occurrences (case-insensitive)
    counter = Counter(c.lower().strip() for c in concepts if len(c) > 3)
    return [theme for theme, count in counter.most_common(10) if count >= 3]


# ---------------------------------------------------------------------------
# Semantic memory search (via memory_index)
# ---------------------------------------------------------------------------


def search_memory(query: str, top_k: int = 5, user_id: str = "ang") -> str:
    """Search across all soul files using vector + keyword hybrid search.

    Returns formatted results for injection into prompts.
    Uses PostgreSQL + pgvector via memory_store, falls back to SQLite memory_index.
    """
    try:
        from memory.store import search_formatted

        return search_formatted(query, top_k=top_k, user_id=user_id)
    except (ImportError, ModuleNotFoundError, ConnectionError, OSError) as e:
        log.warning("Memory search failed: %s", e)
        return ""


def rebuild_memory_index(force: bool = False, user_id: str = "ang") -> int:
    """Rebuild the semantic memory index. Call after major memory changes."""
    try:
        from memory.store import rebuild_index

        return rebuild_index(force=force, user_id=user_id)
    except (ImportError, ModuleNotFoundError, ConnectionError, OSError) as e:
        log.warning("Memory index rebuild failed: %s", e)
        return 0


def auto_flush(context_summary: str):
    """Save important context before it's lost (e.g. before context compaction).

    Call this when an agent session is winding down or context is large.
    Saves to conversations/ archive (NOT memory.md — that's for cognitive insights only).
    """
    if not context_summary or len(context_summary.strip()) < 50:
        return

    # Save as a conversation archive file (indexed by memory_index)
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    path = CONVERSATIONS_DIR / f"flush_{ts}.md"
    _atomic_write(path, f"# Context Flush ({ts})\n\n{context_summary[:2000]}\n")
    log.info("Auto-flush saved to %s", path.name)

    # Trigger async index rebuild (non-blocking)
    try:
        rebuild_memory_index()
    except (ImportError, ModuleNotFoundError, ConnectionError, RuntimeError) as e:
        log.warning("Auto-flush index rebuild failed: %s", e)


# ---------------------------------------------------------------------------
# Episode Archival — save complete conversations for long-term recall
# ---------------------------------------------------------------------------


def save_episode(
    task_id: str,
    title: str,
    messages: list[dict],
    tags: list[str] | None = None,
    user_id: str = "ang",
    verification_proxy: dict | None = None,
):
    """Archive a complete task conversation as a searchable episode.

    Episodes are indexed by memory_index for semantic search, enabling
    Mira to recall past discussions, decisions, and context.
    """
    EPISODES_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    ts = datetime.now().strftime("%H%M")

    # Deduplicate: remove any existing episode for this task_id
    for existing in EPISODES_DIR.glob("*.md"):
        try:
            head = existing.read_text(encoding="utf-8")[:200]
            if f"Task: {task_id}" in head:
                existing.unlink()
                log.info("Replaced existing episode for task %s", task_id)
                break
        except OSError:
            continue

    # Build readable markdown from conversation
    lines = [f"# Episode: {title}", f"*Task: {task_id} | Date: {today}*", ""]
    if tags:
        lines.append(f"Tags: {', '.join(tags)}")
        lines.append("")

    if verification_proxy and verification_proxy.get("proxy_checked"):
        lines.append(
            f"Verification proxy: {verification_proxy['proxy_checked']}"
            f" → assumes: {verification_proxy.get('property_assumed', '')}"
        )
        unverified = verification_proxy.get("unverified_assumptions") or []
        if unverified:
            lines.append(f"Unverified: {', '.join(unverified)}")
        lines.append("")

    for msg in messages:
        sender = msg.get("sender", "?")
        content = msg.get("content", "")
        # Skip status cards
        if content.startswith('{"type":'):
            continue
        msg_ts = msg.get("timestamp", "")[:16]
        lines.append(f"**[{msg_ts}] {sender}**: {content}")
        lines.append("")

    slug = re.sub(r"[^\w\s-]", "", title.lower())[:40].strip().replace(" ", "-")
    filename = f"{today}_{ts}_{slug or task_id}.md"
    path = EPISODES_DIR / filename
    episode_text = "\n".join(lines)
    _atomic_write(path, episode_text)
    log.info("Episode saved: %s (%d messages)", filename, len(messages))
    proxy_detail = ""
    if verification_proxy and verification_proxy.get("proxy_checked"):
        proxy_detail = f" [proxy:{verification_proxy['proxy_checked']}]"
    _log_change("SAVE_EPISODE", filename, title[:60] + proxy_detail)

    # Persist to Postgres for vector search (best-effort)
    try:
        from memory.store import get_store

        store = get_store()
        # Store a summary (first 2000 chars) as an episodic memory entry
        store.remember(
            episode_text[:2000],
            source_type="episode",
            source_id=task_id,
            title=title,
            importance=0.6,
            tags=tags,
            user_id=user_id,
        )
    except (ImportError, ModuleNotFoundError, ConnectionError, OSError) as e:
        log.debug("Postgres episode persist skipped: %s", e)
    return path


# ---------------------------------------------------------------------------
# Content Catalog — structured metadata for all produced content
# ---------------------------------------------------------------------------


def catalog_add(entry: dict):
    """Add an entry to the content catalog.

    Entry should have: type, title, date, path, topics, status.
    Optional: substack_id, description, source_task.
    Deduplicates by (type, title) — updates existing entry if found.
    """
    # Ensure required fields
    entry.setdefault("date", datetime.now().strftime("%Y-%m-%d"))
    entry.setdefault("topics", [])
    entry.setdefault("status", "draft")
    key = (entry.get("type", ""), entry.get("title", ""))

    def _modify(text):
        entries = []
        if text:
            for line in text.strip().splitlines():
                if line.strip():
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        entries = [e for e in entries if (e.get("type", ""), e.get("title", "")) != key]
        entries.append(entry)
        return "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n"

    _locked_read_modify_write(CATALOG_FILE, _modify)
    log.info("Catalog +: [%s] %s", entry.get("type"), entry.get("title", "")[:60])


def catalog_search(query: str, content_type: str | None = None) -> list[dict]:
    """Search the content catalog by keyword. Returns matching entries."""
    if not CATALOG_FILE.exists():
        return []

    query_lower = query.lower()
    results = []
    for line in CATALOG_FILE.read_text(encoding="utf-8").strip().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if content_type and entry.get("type") != content_type:
            continue
        # Match against title, topics, description
        searchable = " ".join(
            [
                entry.get("title", ""),
                " ".join(entry.get("topics", [])),
                entry.get("description", ""),
            ]
        ).lower()
        if query_lower in searchable:
            results.append(entry)

    return results


def catalog_list(content_type: str | None = None) -> list[dict]:
    """List all catalog entries, optionally filtered by type."""
    if not CATALOG_FILE.exists():
        return []

    entries = []
    for line in CATALOG_FILE.read_text(encoding="utf-8").strip().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if content_type and entry.get("type") != content_type:
            continue
        entries.append(entry)

    return entries


# ---------------------------------------------------------------------------
# Proactive Recall — search memory before acting
# ---------------------------------------------------------------------------


def _search_knowledge_files(query: str, max_chars: int = 800) -> str:
    """Simple keyword search through soul/knowledge/ distillation files."""
    knowledge_dir = MEMORY_FILE.parent / "knowledge"
    if not knowledge_dir.exists():
        return ""
    query_words = set(query.lower().split())
    hits = []
    for path in sorted(knowledge_dir.glob("*.md"), reverse=True):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            content_lower = content.lower()
            # Score by keyword overlap
            score = sum(1 for w in query_words if w in content_lower)
            if score > 0:
                hits.append((score, path.name, content))
        except OSError:
            continue
    if not hits:
        return ""
    hits.sort(key=lambda x: -x[0])
    # Return top matches, capped
    result_parts = []
    total = 0
    for _score, name, content in hits[:3]:
        snippet = content[:400].strip()
        if total + len(snippet) > max_chars:
            break
        result_parts.append(f"[{name}] {snippet}")
        total += len(snippet)
    return "\n\n".join(result_parts)


def recall_context(query: str, max_chars: int = 2000, user_id: str = "ang") -> str:
    """Search memory for relevant prior context before starting a task.

    Returns formatted context string for injection into task prompts.
    Searches semantic memory index, content catalog, and knowledge files.
    """
    parts = []

    # 1. Semantic memory search (episodes, journals, reading notes, etc.)
    mem_results = search_memory(query, top_k=3, user_id=user_id)
    if mem_results:
        parts.append("## Relevant memories\n" + mem_results)

    # 2. Distilled knowledge (soul/knowledge/ files)
    knowledge_hits = _search_knowledge_files(query, max_chars=600)
    if knowledge_hits:
        parts.append("## Distilled knowledge\n" + knowledge_hits)

    # 3. Content catalog search
    catalog_hits = catalog_search(query)
    if catalog_hits:
        cat_lines = ["## Related content I've produced"]
        for hit in catalog_hits[:5]:
            cat_lines.append(
                f"- [{hit.get('type')}] \"{hit.get('title')}\" " f"({hit.get('date', '?')}, {hit.get('status', '?')})"
            )
        parts.append("\n".join(cat_lines))

    result = "\n\n".join(parts)
    return result[:max_chars] if result else ""


# ---------------------------------------------------------------------------
# Retention policy — prevent unbounded growth of journal/reading_notes/episodes
# ---------------------------------------------------------------------------

RETENTION_DAYS_JOURNAL = 90  # keep 3 months of daily journals
RETENTION_DAYS_READING_NOTES = 90  # keep 3 months of reading notes
RETENTION_DAYS_EPISODES = 60  # keep 2 months of episodes


def prune_old_files(directory: Path, max_age_days: int, label: str = "") -> int:
    """Delete files older than max_age_days from a date-prefixed directory.

    Files must start with YYYY-MM-DD to be considered for pruning.
    Returns the number of files deleted.
    """
    if not directory.exists():
        return 0

    cutoff = datetime.now() - __import__("datetime").timedelta(days=max_age_days)
    deleted = 0
    for path in directory.glob("*.md"):
        try:
            date_str = path.stem[:10]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date < cutoff:
                path.unlink()
                deleted += 1
        except (ValueError, OSError):
            continue
    if deleted:
        log.info("Retention: pruned %d old %s files (>%d days)", deleted, label or directory.name, max_age_days)
    return deleted


def _collect_expiring_files(directory: Path, max_age_days: int) -> list[Path]:
    """Return files that would be pruned (past retention cutoff)."""
    if not directory.exists():
        return []
    cutoff = datetime.now() - __import__("datetime").timedelta(days=max_age_days)
    expiring = []
    for path in directory.glob("*.md"):
        try:
            date_str = path.stem[:10]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date < cutoff:
                expiring.append(path)
        except (ValueError, OSError):
            continue
    return expiring


def distill_before_delete(user_id: str = "ang") -> int:
    """Extract key insights from expiring files before retention deletes them.

    Reads soon-to-be-deleted journals, episodes, and reading notes in batch,
    asks a local LLM to extract what's worth keeping permanently, and writes
    the result to soul/knowledge/{YYYY-MM}_distill.md.

    Uses oMLX (local) to avoid API cost — this is a background housekeeping
    task, not a user-facing quality-critical operation.

    Returns number of distilled files.
    """
    journal_dir = MEMORY_FILE.parent / "journal"
    knowledge_dir = MEMORY_FILE.parent / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    # Collect all expiring files across the three directories
    expiring: list[tuple[str, Path]] = []
    for label, directory, days in [
        ("journal", journal_dir, RETENTION_DAYS_JOURNAL),
        ("reading_notes", READING_NOTES_DIR, RETENTION_DAYS_READING_NOTES),
        ("episodes", EPISODES_DIR, RETENTION_DAYS_EPISODES),
    ]:
        for f in _collect_expiring_files(directory, days):
            expiring.append((label, f))

    if not expiring:
        return 0

    # Read content in batches (cap total to avoid overwhelming the LLM)
    MAX_BATCH_CHARS = 12000
    batch_text = []
    batch_files = 0
    total_chars = 0
    for label, path in sorted(expiring, key=lambda x: x[1].name):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            # Take first 800 chars of each file to stay within budget
            snippet = content[:800].strip()
            if snippet:
                batch_text.append(f"[{label}: {path.name}]\n{snippet}")
                total_chars += len(snippet)
                batch_files += 1
                if total_chars >= MAX_BATCH_CHARS:
                    break
        except OSError:
            continue

    if not batch_text:
        return 0

    combined = "\n\n---\n\n".join(batch_text)

    prompt = f"""你正在整理即将过期的旧笔记 ({batch_files} 个文件).
这些文件会被删除. 请提炼出值得永久记住的内容.

## 要求
- 只保留有长期价值的洞察、决策、教训、模式
- 忽略日常琐事、临时状态、重复内容
- 每条提炼 1-2 句话, 标注来源文件名
- 如果没有值得保留的内容, 只输出: "无值得保留的内容"
- 用中文输出

## 待处理材料
{combined}

## 输出格式
每条一行:
- **[主题]** 提炼内容 (来源: 文件名)"""

    try:
        from llm import model_think

        result = model_think(prompt, model_name="omlx", timeout=120)
    except Exception as e:
        log.warning("distill_before_delete: LLM call failed: %s", e)
        return 0

    if not result or "无值得保留" in result or len(result) < 50:
        log.info("distill_before_delete: nothing worth keeping from %d files", batch_files)
        return 0

    # Write to knowledge/ with month prefix
    month = datetime.now().strftime("%Y-%m")
    distill_path = knowledge_dir / f"{month}_distill.md"
    header = f"# 知识提炼 {month}\n\n"
    if distill_path.exists():
        existing = distill_path.read_text(encoding="utf-8")
        # Append new section
        new_section = (
            f"\n\n## {datetime.now().strftime('%Y-%m-%d')} " f"(从 {batch_files} 个过期文件提炼)\n\n{result}\n"
        )
        _atomic_write(distill_path, existing + new_section)
    else:
        content = (
            f"{header}" f"## {datetime.now().strftime('%Y-%m-%d')} " f"(从 {batch_files} 个过期文件提炼)\n\n{result}\n"
        )
        _atomic_write(distill_path, content)

    log.info("distill_before_delete: extracted insights from %d files -> %s", batch_files, distill_path.name)

    # Also index in RAG if memory store is available
    try:
        from memory.store import get_store

        store = get_store()
        store.remember(
            content=result[:2000],
            source_type="distill",
            source_id=f"distill_{month}",
            user_id=user_id,
        )
    except Exception as e:
        log.debug("distill RAG indexing failed (non-critical): %s", e)

    return batch_files


def run_retention_policy(user_id: str = "ang"):
    """Distill expiring knowledge, then prune old files.

    Call from journal cycle (daily) to keep disk usage bounded.
    Knowledge is extracted before deletion so nothing is lost silently.
    """
    # Step 1: Distill before delete
    try:
        distilled = distill_before_delete(user_id=user_id)
        if distilled:
            log.info("Retention: distilled %d files before pruning", distilled)
    except Exception as e:
        log.warning("Retention: distill step failed (proceeding with prune): %s", e)

    # Step 2: Prune
    total = 0
    total += prune_old_files(READING_NOTES_DIR, RETENTION_DAYS_READING_NOTES, "reading_notes")
    total += prune_old_files(EPISODES_DIR, RETENTION_DAYS_EPISODES, "episodes")
    journal_dir = MEMORY_FILE.parent / "journal"
    total += prune_old_files(journal_dir, RETENTION_DAYS_JOURNAL, "journal")
    return total


# ---------------------------------------------------------------------------
# Health check — startup visibility into soul file inventory
# ---------------------------------------------------------------------------


def health_check() -> dict:
    """Return {ok: bool, missing: list} for the soul file inventory.

    Checks that each core soul file exists and is non-empty.
    Missing or empty files are listed by name so callers can log them.
    """
    required = {
        "identity.md": IDENTITY_FILE,
        "memory.md": MEMORY_FILE,
        "interests.md": INTERESTS_FILE,
        "worldview.md": WORLDVIEW_FILE,
    }
    missing = []
    for name, path in required.items():
        if not path.exists() or path.stat().st_size == 0:
            missing.append(name)
    return {"ok": len(missing) == 0, "missing": missing}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_or_default(path: Path, default: str) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return default
