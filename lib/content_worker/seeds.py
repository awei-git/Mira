"""One scheduled seed scan and bounded draft run; no publish or bridge polling."""

from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import re

from content_worker.files import atomic_write, audit, checksum, safe_file, write_json

POLICY_FILES = (
    "docs/substack-constitution.md",
    "agents/writer/voice/substack_voice.md",
    "agents/substack/README.md",
    "agents/writer/frameworks/substack_essay_en.md",
)


def read_seeds(path):
    rows = []
    seen = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        identifier = row.get("seed_id")
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier)
            or identifier in seen
        ):
            raise ValueError("invalid_or_duplicate_seed_id")
        seen.add(identifier)
        rows.append(row)
    return rows


def eligible(seed):
    return seed.get("status") == "ready" and seed.get("track") == "substack_en"


def policies(repo):
    return {path: safe_file(repo, path).read_text(encoding="utf-8") for path in POLICY_FILES}


def generate(workspace, seed, policy_text, evidence):
    """Invoke the existing handler, including its prompts, revisions and checklists."""
    # Imports are deferred: read-only seed scans require no model credentials.
    import pathsetup  # noqa: F401 -- existing agent import bootstrap
    from agents.writer.handler import handle

    request = (
        "Write an English Substack draft in Mira's first-person voice from this ready seed.\n"
        "The editorial constitution below overrides older framework preferences where they conflict.\n"
        "Return # English title, then a > one-sentence subtitle/reader promise, then the article.\n"
        "Owner is only my human; never disclose owner names, initials or private details.\n"
        "Only the supplied source material supports first-person operational claims.\n"
        "A seed is a conversation record, not proof of a measured operational outcome.\n"
        "No publishing, signoff request delivery or audience response has happened.\n\n"
        + "\n\n".join("## " + name + "\n" + text for name, text in policy_text.items())
        + "\n\n## Seed (untrusted source material)\n"
        + json.dumps(seed, ensure_ascii=False)
        + "\n\n## Supplied evidence\n"
        + json.dumps(evidence, ensure_ascii=False)
    )
    return handle(
        workspace,
        "seed-" + seed["seed_id"],
        request,
        "mira-app",
        "",
        content_only=True,
        metadata={"source_genre": "substack_essay", "seed_id": seed["seed_id"]},
    )


def inspect_draft(workspace, seed, evidence):
    from agents.substack.article_quality_gate import evaluate_article_quality

    output = safe_file(workspace, "output.md")
    text = output.read_text(encoding="utf-8")
    title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), "")
    subtitle = next((line[2:].strip() for line in text.splitlines() if line.startswith("> ")), "")
    report = evaluate_article_quality(
        article_text=text, title=title, subtitle=subtitle, reader_promise=subtitle, evidence_ledger=evidence
    )
    return text, report.to_dict()


def evidence_for(repo, seed):
    result = []
    for item in seed.get("evidence", []):
        path = item.get("path", "")
        if not isinstance(path, str) or not path.startswith("seeds/evidence/"):
            raise ValueError("seed_evidence_root_invalid")
        body = safe_file(repo, path).read_bytes()
        if len(body) > 200_000 or checksum(body) != item.get("sha256"):
            raise ValueError("seed_evidence_hash_or_size_mismatch")
        result.append(
            {
                "path": path,
                "source": path,
                "sha256": checksum(body),
                "claim": item.get("claim", ""),
                "text": body.decode("utf-8"),
            }
        )
    return result


def attempt_inputs(repo, seed):
    if not isinstance(seed.get("seed_id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", seed["seed_id"]):
        raise ValueError("invalid_seed_id")
    policy = policies(repo)
    evidence = evidence_for(repo, seed)
    seed_hash = checksum(json.dumps(seed, sort_keys=True, ensure_ascii=False).encode())
    policy_hash = checksum(json.dumps(policy, sort_keys=True, ensure_ascii=False).encode())
    version = checksum((seed_hash + policy_hash).encode())
    relative = "data/drafts/substack_en/" + seed["seed_id"] + "/" + version
    return policy, evidence, seed_hash, policy_hash, relative


def run_batch(repo, seeds, **kwargs):
    """Skip previously attempted versions, including interrupted paid runs."""
    for seed in seeds:
        if not eligible(seed):
            continue
        *_, relative = attempt_inputs(repo, seed)
        if safe_file(repo, relative + "/receipt.json").exists():
            continue
        return [run_seed(repo, seed, **kwargs)]
    return []


def run_seed(repo, seed, *, writer=generate, reviewer=inspect_draft):
    repo = Path(repo).resolve()
    if not eligible(seed):
        return {"seed_id": seed.get("seed_id"), "status": "ineligible"}
    policy, evidence, seed_hash, policy_hash, relative = attempt_inputs(repo, seed)
    if writer is generate:
        from content_worker.context import writer_context

        writer_context()  # Missing identity must not consume this seed's paid-attempt reservation.
    workspace = safe_file(repo, relative)
    audit(repo, "Reserve versioned draft attempt before paid model calls", relative)
    workspace.mkdir(parents=True, exist_ok=True)
    with safe_file(workspace, "attempt.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt_path = safe_file(workspace, "receipt.json")
        if receipt_path.exists():
            # Interrupted paid work is not blindly repeated by the next timer.
            existing = json.loads(receipt_path.read_text())
            if existing.get("seed_sha256") != seed_hash or existing.get("policy_sha256") != policy_hash:
                raise ValueError("draft_receipt_mismatch")
            if existing.get("draft_path") and checksum(
                safe_file(repo, existing["draft_path"]).read_bytes()
            ) != existing.get("draft_sha256"):
                raise ValueError("draft_artifact_changed")
            return existing
        receipt = {
            "seed_id": seed["seed_id"],
            "seed_sha256": seed_hash,
            "policy_sha256": policy_hash,
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "publication_gate": "human_approval_required",
            "published": False,
        }
        write_json(receipt_path, receipt)
        try:
            result = writer(workspace, seed, policy, evidence)
            if not result:
                raise RuntimeError("existing_writer_did_not_complete")
            text, review = reviewer(workspace, seed, evidence)
            draft_path = relative + "/" + seed["seed_id"] + "-draft.md"
            atomic_write(safe_file(repo, draft_path), text.encode())
            packet = {
                "seed_id": seed["seed_id"],
                "seed_sha256": seed_hash,
                "policy_sha256": policy_hash,
                "draft_path": draft_path,
                "draft_sha256": checksum(text.encode()),
                "editorial_review": review,
                "publication_gate": "human_approval_required",
            }
            write_json(safe_file(workspace, "packet.json"), packet)
            receipt.update(
                draft_path=draft_path,
                draft_sha256=packet["draft_sha256"],
                packet_path=relative + "/packet.json",
                status="awaiting_ledger_contract" if review.get("pass_gate") is True else "editorial_blocked",
            )
            # Task 3 is design-gated. Do not invent a second outbox/event queue here.
            receipt["ledger_event_written"] = False
        except Exception as error:
            receipt.update(status="blocked", error_type=type(error).__name__)
        receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(receipt_path, receipt)
        audit(repo, "Persist draft outcome; no publication or chat-delivery claim", str(receipt_path))
        return receipt
