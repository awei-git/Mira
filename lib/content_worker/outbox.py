"""Deterministic three-section daily outbox; never a second task queue."""

from datetime import date, datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from content_worker.files import atomic_write, audit, checksum, safe_file


def daily_records(root, day):
    """Summarize actual worker receipts; never upgrade an idea to verified learning."""
    date.fromisoformat(day)
    root = Path(root)
    records = {"journal": [], "verified_learnings": [], "sync": []}
    drafts = safe_file(root, "data/drafts/substack_en")
    for path in sorted(drafts.glob("*/*/receipt.json")):
        relative = str(path.relative_to(root))
        row = json.loads(safe_file(root, relative).read_text())
        timestamp = datetime.fromisoformat(row.get("finished_at") or row["started_at"])
        if timestamp.tzinfo is None:
            raise ValueError("receipt_requires_timezone")
        if timestamp.astimezone(ZoneInfo("America/New_York")).date().isoformat() != day:
            continue
        records["journal"].append({"text": f"Seed {row['seed_id']}: {row['status']}. Receipt: {relative}."})
        next_step = {
            "running": "Attempt is running or interrupted; reconcile its receipt before retrying.",
            "blocked": "Writer attempt failed; operator review is required before retrying.",
            "editorial_blocked": "Editorial gate failed; revision is required.",
            "awaiting_ledger_contract": "Draft passed local checks; ledger contract and chat return are pending.",
            "awaiting_ledger_event": "Draft is saved; retry only its ledger event, not the writer.",
            "draft_ready_for_signoff": "Signoff event is in the ledger; Muse presentation and human approval are pending.",
        }.get(row["status"], "Unknown receipt status; operator reconciliation is required.")
        records["sync"].append(
            {"text": f"Seed {row['seed_id']}: {next_step} No publication or chat delivery is implied."}
        )
    return records


def write_outbox(root, day, records):
    date.fromisoformat(day)
    root = Path(root)
    if set(records) != {"journal", "verified_learnings", "sync"}:
        raise ValueError("outbox_requires_three_sections")
    sections = []
    for key, title in (("journal", "journal"), ("verified_learnings", "verified learnings"), ("sync", "需同步事项")):
        entries = records[key]
        if not isinstance(entries, list) or len(entries) > 200:
            raise ValueError("outbox_entries_invalid")
        lines = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("text"), str) or not entry["text"].strip():
                raise ValueError("outbox_entry_invalid")
            text = " ".join(entry["text"].split())
            if len(text) > 4000:
                raise ValueError("outbox_entry_too_long")
            reference = ""
            if key == "verified_learnings":
                if entry.get("verifier") not in {"automated_check", "independent_review"}:
                    raise ValueError("learning_requires_independent_verification")
                evidence = entry.get("evidence_path", "")
                if not evidence.startswith(("data/receipts/", "data/drafts/")):
                    raise ValueError("learning_receipt_root_invalid")
                body = safe_file(root, evidence).read_bytes()
                if checksum(body) != entry.get("evidence_sha256"):
                    raise ValueError("learning_evidence_hash_mismatch")
                reference = f" (receipt: {evidence}; sha256: {entry['evidence_sha256']})"
            lines.append("- " + text + reference)
        sections.append("## " + title + "\n\n" + ("\n".join(lines) if lines else "None recorded."))
    target = safe_file(root, "data/outbox/" + day + ".md")
    body = ("# " + day + "\n\n" + "\n\n".join(sections) + "\n").encode()
    if target.exists() and target.read_bytes() == body:
        return target
    audit(root, "Write daily return outbox from explicit records", str(target))
    atomic_write(target, body)
    return target
