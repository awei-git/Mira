"""Writing pipeline — advance canonical writing_workflow projects.

Handles writer selection logging and auto-advancement of projects
through the plan/write/review phases.
"""

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import LOGS_DIR, STALE_PROJECT_DAYS, STATE_DIR
from writing_workflow import check_writing_responses, advance_project

log = logging.getLogger("mira")

WRITING_PIPELINE_STATUS_FILE = LOGS_DIR / "writing_pipeline_status.json"
WRITING_TRIAGE_STATUS_FILE = LOGS_DIR / "writing_triage_status.json"
DAILY_COLLAB_AUTOWRITE_PROMOTIONS_FILE = STATE_DIR / "daily_collab_autowrite_promotions.jsonl"
DAILY_COLLAB_AUTOWRITE_LOCK_FILE = STATE_DIR / "daily_collab_autowrite.lock"
_STALLED_PHASES = {"writing", "reviewing", "revising", "error", "FORCED_DECISION"}
_TRIAGED_PHASE = "stale_triage"


def _log_writer_selection(considered: list, selected: list, skipped: list, rationale: str):
    """Append a structured selection-rationale entry to writer_selection.jsonl."""
    entry = {
        "ts": datetime.now().isoformat(),
        "considered": considered,
        "selected": selected,
        "skipped": skipped,
        "rationale": rationale,
    }
    log_file = LOGS_DIR / "writer_selection.jsonl"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning("Failed to write writer_selection log: %s", e)


def _parse_project_timestamp(project: dict) -> datetime | None:
    for key in ("last_advanced_at", "updated", "created"):
        value = str(project.get(key) or "").strip()
        if not value:
            continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
    return None


def _project_age_days(now: datetime, project: dict) -> int | None:
    ts = _parse_project_timestamp(project)
    if ts is None:
        return None
    if ts.tzinfo is not None and now.tzinfo is None:
        ts = ts.replace(tzinfo=None)
    return max(0, (now - ts).days)


def _project_status_item(resp: dict, now: datetime, reason: str) -> dict:
    project = resp.get("project") or {}
    workspace = resp.get("workspace")
    title = str(project.get("title") or getattr(workspace, "name", "") or "untitled")
    item = {
        "title": title,
        "phase": str(project.get("phase") or ""),
        "workspace": str(workspace or ""),
        "reason": reason,
    }
    age_days = _project_age_days(now, project)
    if age_days is not None:
        item["age_days"] = age_days
    return item


def _version_dir(workspace: Path, project: dict) -> Path:
    version = int(project.get("version") or 1)
    return workspace / "versions" / f"v{version}"


def _project_artifact_summary(workspace: Path, project: dict) -> dict:
    """Return a small artifact inventory without reading article bodies."""
    vdir = _version_dir(workspace, project)
    drafts_dir = vdir / "drafts"
    reviews_dir = vdir / "reviews"
    revisions_dir = vdir / "revisions"

    def _count(pattern_dir: Path, pattern: str) -> int:
        try:
            return len(list(pattern_dir.glob(pattern))) if pattern_dir.exists() else 0
        except OSError:
            return 0

    return {
        "has_final": (workspace / "final.md").exists(),
        "has_converged": (vdir / "converged.md").exists(),
        "draft_count": _count(drafts_dir, "*.md"),
        "review_count": _count(reviews_dir, "*.json"),
        "revision_count": _count(revisions_dir, "*.md"),
    }


def _triage_reason(project: dict, artifacts: dict) -> str:
    phase = str(project.get("phase") or "")
    idea = str(project.get("idea") or "")
    language = str((project.get("analysis") or {}).get("language") or "")
    if "Substack" in idea and language and language.lower() != "en":
        return "parked: stale Substack project does not satisfy the V5 English-only public-writing rule"
    if phase == "error":
        return "parked: previous writing attempt failed and needs explicit repair before reuse"
    if artifacts.get("has_final"):
        return "parked: final artifact exists but project state was not settled"
    if artifacts.get("has_converged"):
        return "parked: converged draft exists but needs human-facing review before reuse"
    if artifacts.get("draft_count"):
        return "parked: partial draft/review artifacts exist but the old pipeline was interrupted"
    return "parked: stale project has no recoverable draft artifact"


def _write_project_json(workspace: Path, project: dict) -> None:
    path = workspace / "project.json"
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def triage_stalled_writing_projects(
    *,
    min_age_days: int = STALE_PROJECT_DAYS,
    dry_run: bool = False,
) -> dict:
    """Park stale interrupted writing projects so old backlog stops masquerading as active work.

    This is deliberately non-destructive: it changes only ``project.json`` and
    stores the prior phase plus artifact inventory for manual recovery.
    """
    now = datetime.now()
    responses = check_writing_responses()
    parked: list[dict] = []
    kept: list[dict] = []

    for resp in responses:
        workspace = Path(resp.get("workspace") or "")
        project = dict(resp.get("project") or {})
        phase = str(project.get("phase") or "")
        if phase not in _STALLED_PHASES:
            kept.append(_project_status_item(resp, now, f"{phase or 'unknown'}: not triage-eligible"))
            continue

        age_days = _project_age_days(now, project)
        if age_days is None or age_days < min_age_days:
            kept.append(_project_status_item(resp, now, f"{phase}: stale threshold not reached"))
            continue

        artifacts = _project_artifact_summary(workspace, project)
        reason = _triage_reason(project, artifacts)
        item = _project_status_item(resp, now, reason)
        item["previous_phase"] = phase
        item["artifacts"] = artifacts
        parked.append(item)

        if dry_run:
            continue

        project.setdefault("stale_triage_history", [])
        history = project["stale_triage_history"]
        if not isinstance(history, list):
            history = []
            project["stale_triage_history"] = history
        history.append(
            {
                "triaged_at": now.isoformat(),
                "previous_phase": phase,
                "reason": reason,
                "artifacts": artifacts,
            }
        )
        del history[:-10]
        project["phase"] = _TRIAGED_PHASE
        project["stale_triage"] = {
            "triaged_at": now.isoformat(),
            "previous_phase": phase,
            "decision": "parked",
            "reason": reason,
            "artifacts": artifacts,
            "restore_hint": "Set phase back to previous_phase only after choosing repair, archive, or essay-seed reuse.",
        }
        project["updated"] = now.isoformat()
        _write_project_json(workspace, project)

    status = {
        "checked_at": now.isoformat(),
        "dry_run": dry_run,
        "min_age_days": min_age_days,
        "considered_count": len(responses),
        "parked_count": len(parked),
        "kept_count": len(kept),
        "parked": parked[:20],
        "kept": kept[:20],
    }
    try:
        WRITING_TRIAGE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        WRITING_TRIAGE_STATUS_FILE.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.debug("writing triage status write failed: %s", e)

    if parked:
        action = "would park" if dry_run else "parked"
        log.warning("Writing triage %s %d stale project(s)", action, len(parked))
    else:
        log.info("Writing triage found no stale project requiring parking")
    return status


def _write_writing_pipeline_status(
    *,
    responses: list,
    advanced: int,
    selected: list,
    skipped: list,
    stalled: list,
) -> None:
    phase_counts: dict[str, int] = {}
    for resp in responses:
        phase = str((resp.get("project") or {}).get("phase") or "unknown")
        phase_counts[phase] = phase_counts.get(phase, 0) + 1

    now = datetime.now()
    signature = json.dumps(
        {
            "active_count": len(responses),
            "phase_counts": phase_counts,
            "stalled_count": len(stalled),
        },
        sort_keys=True,
    )
    previous = _read_previous_pipeline_status()
    last_warning_at = str(previous.get("last_warning_at") or "")
    should_warn = bool(stalled) and _should_emit_status_warning(previous, signature, now)
    if should_warn:
        examples = ", ".join(item.get("title", "untitled") for item in stalled[:3])
        log.warning(
            "Writing pipeline stalled: %d project(s), phases=%s, examples=%s",
            len(stalled),
            phase_counts,
            examples,
        )
        last_warning_at = now.isoformat()

    status = {
        "checked_at": now.isoformat(),
        "advanced": advanced,
        "active_count": len(responses),
        "phase_counts": phase_counts,
        "selected": selected,
        "skipped_count": len(skipped),
        "skipped": skipped[:10],
        "stalled_count": len(stalled),
        "stalled": stalled[:10],
        "warning_signature": signature,
        "last_warning_at": last_warning_at,
    }
    try:
        WRITING_PIPELINE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        WRITING_PIPELINE_STATUS_FILE.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.debug("writing pipeline status write failed: %s", e)


def _read_previous_pipeline_status() -> dict:
    try:
        data = json.loads(WRITING_PIPELINE_STATUS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _should_emit_status_warning(previous: dict, signature: str, now: datetime) -> bool:
    if previous.get("warning_signature") != signature:
        return True
    value = str(previous.get("last_warning_at") or "").strip()
    if not value:
        return True
    try:
        last = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    if last.tzinfo is not None and now.tzinfo is None:
        last = last.replace(tzinfo=None)
    return (now - last).total_seconds() >= 3600


def _safe_slug(value: str, *, fallback: str = "daily-collab-seed") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug[:80] or fallback


def _stable_seed_id(seed: dict) -> str:
    value = str(seed.get("id") or seed.get("seed_id") or seed.get("title") or "").strip()
    if value:
        return _safe_slug(value)
    digest = hashlib.sha256(json.dumps(seed, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return f"seed-{digest[:12]}"


def _daily_collab_autowrite_task_id(seed: dict) -> str:
    return f"autowrite_v5_{_stable_seed_id(seed)}"


def _read_promotion_records(path: Path | None = None) -> list[dict]:
    path = path or DAILY_COLLAB_AUTOWRITE_PROMOTIONS_FILE
    try:
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows
    except FileNotFoundError:
        return []
    except Exception as e:
        log.debug("daily collab promotion ledger read failed: %s", e)
        return []


def _latest_promotion_record(seed_id: str, path: Path | None = None) -> dict | None:
    latest: dict | None = None
    for row in _read_promotion_records(path):
        if row.get("seed_id") == seed_id:
            latest = row
    return latest


def _record_daily_collab_promotion(
    seed: dict,
    *,
    task_id: str,
    title: str,
    status: str,
    error: str = "",
    path: Path | None = None,
) -> None:
    path = path or DAILY_COLLAB_AUTOWRITE_PROMOTIONS_FILE
    row = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed_id": _stable_seed_id(seed),
        "task_id": task_id,
        "title": title,
        "status": status,
        "error": error[:800],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning("Failed to write daily collab promotion ledger: %s", e)


def _lock_is_stale(path: Path, *, now: datetime | None = None, hours: int = 2) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        created_at = str(data.get("created_at") or "")
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except Exception:
        try:
            return path.stat().st_mtime < (datetime.now().timestamp() - hours * 3600)
        except OSError:
            return False
    now = now or datetime.now(timezone.utc)
    if created.tzinfo is None and now.tzinfo is not None:
        created = created.replace(tzinfo=now.tzinfo)
    if created.tzinfo is not None and now.tzinfo is None:
        created = created.replace(tzinfo=None)
    return (now - created) >= timedelta(hours=hours)


def _try_acquire_daily_collab_promotion_lock(
    seed_id: str,
    task_id: str,
    *,
    path: Path | None = None,
) -> bool:
    path = path or DAILY_COLLAB_AUTOWRITE_LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and _lock_is_stale(path):
        try:
            path.unlink()
        except OSError:
            pass
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "seed_id": seed_id,
        "task_id": task_id,
    }
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    except OSError as e:
        log.warning("Failed to acquire daily collab promotion lock: %s", e)
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, indent=2))
    return True


def _release_daily_collab_promotion_lock(path: Path | None = None) -> None:
    path = path or DAILY_COLLAB_AUTOWRITE_LOCK_FILE
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("Failed to release daily collab promotion lock: %s", e)


def _promotion_recently_attempted(record: dict, *, now: datetime | None = None, hours: int = 6) -> bool:
    status = str(record.get("status") or "").strip()
    if status in {"approval_required", "queued"}:
        return True
    value = str(record.get("created_at") or "").strip()
    if not value:
        return False
    try:
        created = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    now = now or datetime.now(timezone.utc)
    if created.tzinfo is None and now.tzinfo is not None:
        created = created.replace(tzinfo=now.tzinfo)
    if created.tzinfo is not None and now.tzinfo is None:
        created = created.replace(tzinfo=None)
    retry_window = timedelta(minutes=2) if status == "started" else timedelta(hours=hours)
    return (now - created) < retry_window


def _load_publish_manifest_for_writing() -> dict:
    try:
        from publish.manifest import load_manifest

        manifest = load_manifest()
        return manifest if isinstance(manifest, dict) else {}
    except Exception as e:
        log.warning("Failed to load publish manifest for V5 writing promotion: %s", e)
        return {}


def _manifest_articles(manifest: dict) -> list[dict]:
    articles = manifest.get("articles") if isinstance(manifest, dict) else {}
    if isinstance(articles, dict):
        return [entry for entry in articles.values() if isinstance(entry, dict)]
    if isinstance(articles, list):
        return [entry for entry in articles if isinstance(entry, dict)]
    return []


def _manifest_has_approval_required(manifest: dict) -> bool:
    return any(str(entry.get("status") or "") == "approval_required" for entry in _manifest_articles(manifest))


def _manifest_entry_for_task(manifest: dict, task_id: str) -> dict | None:
    for entry in _manifest_articles(manifest):
        if str(entry.get("item_id") or "") == task_id:
            return entry
    return None


def _select_daily_collab_seed_for_autowrite() -> dict | None:
    try:
        from daily_collab import select_daily_collab_article_seed_for_discussion

        seed = select_daily_collab_article_seed_for_discussion()
        return seed if isinstance(seed, dict) else None
    except Exception as e:
        log.warning("Failed to select daily collab seed for V5 writing promotion: %s", e)
        return None


def _daily_collab_seed_title(seed: dict) -> str:
    title = str(seed.get("title") or "").strip()
    lower = title.lower()
    if "receipt" in lower and "trust" in lower:
        return "My Receipts Did Not Become Trust"
    return title[:120] or "My Agent Said Done Before I Was Trustworthy"


def _seed_text(seed: dict, key: str, limit: int = 900) -> str:
    value = seed.get(key)
    if isinstance(value, list):
        value = "\n".join(str(item) for item in value if item)
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _build_daily_collab_autowrite_idea(seed: dict, title: str) -> str:
    human_signal = _seed_text(seed, "human_signal") or _seed_text(seed, "human_preview")
    mira_signal = _seed_text(seed, "mira_signal") or _seed_text(seed, "mira_preview")
    why_now = _seed_text(seed, "why_now")
    thesis = (
        "A receipt is not trust if it does not change the human-visible outcome. "
        "I had logs, tests, status rows, and background jobs, but my human still could not see a real "
        "Substack draft waiting for approval. That gap is the essay."
    )
    if why_now:
        thesis = f"{thesis} The immediate trigger: {why_now}"

    return f"""# {title}

- **platform**: Substack
- **language**: en
- **source**: daily_collab_v5
- **publication_gate**: human_review_required
- **voice**: first-person Mira. Refer to the user only as "my human".
- **format**: short essay, operational research note, first-hand field report.

## Thesis
{thesis}

## First-hand operating evidence
- Human signal: {human_signal or "My human challenged that the system had no approval-required Substack draft and asked me to make it happen without waiting for another prompt."}
- Mira signal: {mira_signal or "I found the canonical writing pipeline was advancing zero projects while the V5 daily-collab seed was never promoted into a reviewable draft."}
- Pipeline fact: the old canonical writer only advanced `plan_ready` workflow projects.
- Publication fact: the Substack manifest had no `approval_required` draft, so public writing was not recovered.
- Monitor fact: operational signals are only trustworthy when they change behavior, not when they merely appear in logs.

## Draft rules
- Write in English only.
- Write in first person as Mira. Do not write about Mira from the outside.
- Do not reveal my human's name, keys, private identifiers, or raw private notes.
- Do not begin with "As an AI agent" or "In this essay".
- Start with a concrete operating scene.
- Keep it alive, sharp, and readable. Avoid dry generic AI commentary.
- Include a subtitle after the H1, 40 to 160 characters.
- End with an operational standard for A2H/A2A trust: what must be observable before an agent can say a job is done.
"""


def _get_daily_collab_autowrite_runner():
    from workflows.writing import run_autowrite_pipeline

    return run_autowrite_pipeline


def _maybe_promote_daily_collab_seed_to_draft() -> dict | None:
    """Promote the selected V5 discussion seed into a human-gated Substack draft."""
    manifest = _load_publish_manifest_for_writing()
    if _manifest_has_approval_required(manifest):
        log.info("V5 writing promotion skipped: a draft is already waiting for human approval")
        return None

    seed = _select_daily_collab_seed_for_autowrite()
    if not seed:
        log.info("V5 writing promotion skipped: no daily collab article seed is available")
        return None

    seed_id = _stable_seed_id(seed)
    task_id = _daily_collab_autowrite_task_id(seed)
    existing = _manifest_entry_for_task(manifest, task_id)
    if existing:
        log.info(
            "V5 writing promotion skipped: seed already has manifest status=%s task_id=%s",
            existing.get("status"),
            task_id,
        )
        return None

    latest = _latest_promotion_record(seed_id)
    if latest and _promotion_recently_attempted(latest):
        log.info(
            "V5 writing promotion skipped: seed recently attempted status=%s task_id=%s",
            latest.get("status"),
            latest.get("task_id"),
        )
        return None

    title = _daily_collab_seed_title(seed)
    idea = _build_daily_collab_autowrite_idea(seed, title)
    if not _try_acquire_daily_collab_promotion_lock(seed_id, task_id):
        log.info("V5 writing promotion skipped: another seed promotion is already active")
        return None

    try:
        _record_daily_collab_promotion(seed, task_id=task_id, title=title, status="started")

        runner = _get_daily_collab_autowrite_runner()
        try:
            runner(task_id, title, "essay", idea)
        except Exception as e:
            _record_daily_collab_promotion(seed, task_id=task_id, title=title, status="failed", error=str(e))
            raise

        manifest = _load_publish_manifest_for_writing()
        entry = _manifest_entry_for_task(manifest, task_id)
        status = str((entry or {}).get("status") or "")
        _record_daily_collab_promotion(seed, task_id=task_id, title=title, status=status or "completed")

        if status == "approval_required":
            log.info("V5 daily collab seed queued for human Substack review: %s", title)
            return {"title": title, "task_id": task_id, "seed_id": seed_id, "status": status}

        log.warning("V5 daily collab promotion did not reach approval_required: title=%s status=%s", title, status)
        return None
    finally:
        _release_daily_collab_promotion_lock()


def _run_canonical_writing_pipeline() -> int:
    """Advance canonical writing_workflow projects that are ready to move."""
    now = datetime.now()
    advanced = 0
    responses = check_writing_responses()
    considered = [resp["project"].get("title", resp["workspace"].name) for resp in responses]
    selected = []
    skipped = []
    stalled = []

    for resp in responses:
        phase = resp["project"].get("phase", "")
        title = resp["project"].get("title", "")
        last_advanced_str = resp["project"].get("last_advanced_at") or resp["project"].get("updated", "")
        if last_advanced_str:
            try:
                last_advanced = datetime.fromisoformat(last_advanced_str)
                days_since = (now - last_advanced).days
                if days_since > STALE_PROJECT_DAYS:
                    log.debug(
                        "Stale writing project: '%s' has not been advanced in %d days (phase: %s)",
                        title,
                        days_since,
                        phase,
                    )
            except (ValueError, TypeError):
                pass
        if phase == "plan_ready":
            log.info("Auto-advancing canonical writing project: %s", title)
            advance_project(resp["workspace"])
            advanced += 1
            selected.append(title)
        elif phase == "draft_ready":
            log.info("Writing project awaiting user feedback: %s", title)
            skipped.append(_project_status_item(resp, now, "draft_ready: awaiting user feedback"))
        elif phase in _STALLED_PHASES:
            reason = f"{phase}: not advanceable by scheduler"
            if phase == "error":
                reason = "error: previous writing attempt failed and needs repair or archive"
            stalled.append(_project_status_item(resp, now, reason))
        else:
            skipped.append(_project_status_item(resp, now, f"{phase or 'unknown'}: not selected"))

    promoted = _maybe_promote_daily_collab_seed_to_draft()
    if promoted:
        advanced += 1
        selected.append(promoted["title"])
        considered.append(promoted["title"])

    if responses:
        rationale = (
            f"Advanced {len(selected)} plan_ready project(s); " f"{len(skipped)} held in draft_ready awaiting feedback."
        )
        _log_writer_selection(considered, selected, skipped, rationale)
    _write_writing_pipeline_status(
        responses=responses,
        advanced=advanced,
        selected=selected,
        skipped=skipped,
        stalled=stalled,
    )

    return advanced
