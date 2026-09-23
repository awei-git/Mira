"""One scheduled seed scan and bounded draft run; no publish or bridge polling."""

from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import re
from os import getenv

from content_worker.files import atomic_write, audit, checksum, safe_file, write_json
from content_worker import podcast

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
    return seed.get("status") == "ready" and (seed.get("track") == "substack_en" or podcast.is_podcast(seed))


def policies(repo, seed=None):
    paths = podcast.POLICY_FILES if seed and podcast.is_podcast(seed) else POLICY_FILES
    return {path: safe_file(repo, path).read_text(encoding="utf-8") for path in paths}


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
    metadata = {"source_genre": "substack_essay", "seed_id": seed["seed_id"]}
    if podcast.is_podcast(seed):
        request = podcast.request(seed, policy_text, evidence)
        metadata.update(source_genre="podcast_script", output_language="zh")
    return handle(
        workspace,
        "seed-" + seed["seed_id"],
        request,
        "mira-app",
        "",
        content_only=True,
        metadata=metadata,
    )


def inspect_draft(workspace, seed, evidence):
    from agents.substack.article_quality_gate import evaluate_article_quality

    output = safe_file(workspace, "output.md")
    text = output.read_text(encoding="utf-8")
    if podcast.is_podcast(seed):
        spec = json.loads(safe_file(workspace, "podcast-constraints.json").read_text())
        return text, podcast.inspect(text, spec)
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
    policy = policies(repo, seed)
    if podcast.is_podcast(seed):
        podcast.constraints(seed, policy)
    evidence = evidence_for(repo, seed)
    seed_hash = checksum(json.dumps(seed, sort_keys=True, ensure_ascii=False).encode())
    policy_hash = checksum(json.dumps(policy, sort_keys=True, ensure_ascii=False).encode())
    version = checksum((seed_hash + policy_hash).encode())
    directory = "zh" if podcast.is_podcast(seed) else "substack_en"
    relative = "data/drafts/" + directory + "/" + seed["seed_id"] + "/" + version
    return policy, evidence, seed_hash, policy_hash, relative


def open_ledger(repo):
    from obligations import Ledger

    return Ledger(getenv("MIRA_LEDGER_PATH", "/var/lib/mira-obligations/ledger.sqlite3"), repo)


def seed_job(repo, seed, ledger):
    _, _, seed_hash, policy_hash, relative = attempt_inputs(repo, seed)
    key = "seed.draft:" + seed["seed_id"] + ":" + seed_hash + ":" + policy_hash
    payload = {
        "seed_id": seed["seed_id"],
        "seed_sha256": seed_hash,
        "policy_sha256": policy_hash,
        "draft_dir": relative,
    }
    if podcast.is_podcast(seed):
        payload.update(track="zh", kind="podcast_script")
    existing = ledger.by_key(key)
    if existing:
        if existing["kind"] != "seed.draft" or existing["owner"] != "mira-aws" or existing["payload"] != payload:
            raise ValueError("seed_obligation_conflict")
        return existing
    return ledger.create("seed.draft", "mira-aws", payload, key, "mira-aws")


def run_batch(repo, seeds, *, ledger=None, **kwargs):
    """One new attempt or receipt reconciliation per invocation, in one ledger."""
    for seed in seeds:
        if not eligible(seed):
            continue
        ledger = ledger or open_ledger(repo)
        job = seed_job(repo, seed, ledger)
        if job["status"] in {"succeeded", "blocked", "failed", "cancelled"}:
            continue
        packet = safe_file(repo, job["payload"]["draft_dir"] + "/packet.json")
        if job["status"] in {"claimed", "running"} and job["lease_expires_at"] > ledger.now() and not packet.exists():
            continue
        return [run_seed(repo, seed, ledger=ledger, **kwargs)]
    return []


def run_queue(repo, *, ledger=None, **kwargs):
    """Poll the one ledger once; only exact deployed English seed versions run."""
    ledger = ledger or open_ledger(repo)
    for current in read_seeds(safe_file(repo, "seeds/seeds.jsonl")):
        if not eligible(current):
            continue
        _, _, seed_hash, policy_hash, relative = attempt_inputs(repo, current)
        expected_key = "seed.draft:" + current["seed_id"] + ":" + seed_hash + ":" + policy_hash
        job = ledger.by_key(expected_key)
        if job is None or job["status"] not in {"queued", "claimed", "running"}:
            continue
        if (
            job["status"] in {"claimed", "running"}
            and job["lease_expires_at"] > ledger.now()
            and not safe_file(repo, relative + "/packet.json").exists()
        ):
            continue
        return [run_seed(repo, current, ledger=ledger, **kwargs)]
    return []


def run_seed(repo, seed, *, writer=generate, reviewer=inspect_draft, ledger=None):
    repo = Path(repo).resolve()
    if not eligible(seed):
        return {"seed_id": seed.get("seed_id"), "status": "ineligible"}
    policy, evidence, seed_hash, policy_hash, relative = attempt_inputs(repo, seed)
    ledger = ledger or open_ledger(repo)
    job = seed_job(repo, seed, ledger)
    if job["status"] == "queued" and not safe_file(repo, relative + "/receipt.json").exists() and writer is generate:
        from content_worker.context import writer_context

        writer_context()  # Missing identity must not consume a paid-attempt reservation.
    workspace = safe_file(repo, relative)
    audit(repo, "Reserve versioned draft attempt before paid model calls", relative)
    workspace.mkdir(parents=True, exist_ok=True)
    with safe_file(workspace, "attempt.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt_path = safe_file(workspace, "receipt.json")
        existing = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
        if existing:
            if existing.get("seed_sha256") != seed_hash or existing.get("policy_sha256") != policy_hash:
                raise ValueError("draft_receipt_mismatch")
            if existing.get("draft_path") and checksum(
                safe_file(repo, existing["draft_path"]).read_bytes()
            ) != existing.get("draft_sha256"):
                raise ValueError("draft_artifact_changed")
        if job["status"] == "succeeded":
            job = ledger.complete_draft(job["id"], "mira-aws", job["revision"])
        elif (
            safe_file(workspace, "packet.json").exists()
            and job["status"] in {"claimed", "running"}
            and job["lease_expires_at"] > ledger.now()
        ):
            job = ledger.complete_draft(job["id"], "mira-aws", job["revision"])
        elif existing or job["status"] in {"claimed", "running"}:
            job = ledger.reconcile(job["id"], "mira-aws", job["revision"])
        if job["status"] == "succeeded":
            receipt = existing or {
                "seed_id": seed["seed_id"],
                "seed_sha256": seed_hash,
                "policy_sha256": policy_hash,
                "finished_at": ledger.now(),
                "published": False,
            }
            receipt.update(
                status="draft_ready_for_signoff",
                ledger_event_written=True,
                obligation_id=job["id"],
                event_id=job["result"]["event_id"],
                draft_path=job["result"]["draft_path"],
                draft_sha256=job["result"]["draft_sha256"],
            )
            write_json(receipt_path, receipt)
            audit(repo, "Reconcile completed draft event without rerunning writer", relative)
            return receipt
        if job["status"] != "queued":
            return {"seed_id": seed["seed_id"], "status": "blocked", "obligation_id": job["id"], "published": False}
        job = ledger.claim(job["id"], "mira-aws", job["revision"])
        job = ledger.transition(job["id"], "mira-aws", job["revision"], "running")
        receipt = {
            "seed_id": seed["seed_id"],
            "seed_sha256": seed_hash,
            "policy_sha256": policy_hash,
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "publication_gate": "human_approval_required",
            "published": False,
            "obligation_id": job["id"],
        }
        write_json(receipt_path, receipt)
        try:
            from content_worker.model_guard import model_guard

            route = getenv("MIRA_PODCAST_MODEL_ROUTE", "gpt") if podcast.is_podcast(seed) else None
            if podcast.is_podcast(seed):
                write_json(safe_file(workspace, "podcast-constraints.json"), podcast.constraints(seed, policy))
            with model_guard(ledger, job, repo, route=route):
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
            if podcast.is_podcast(seed):
                packet.update(track="zh", kind="podcast_script")
                receipt.update(track="zh", kind="podcast_script")
            write_json(safe_file(workspace, "packet.json"), packet)
            receipt.update(
                draft_path=draft_path,
                draft_sha256=packet["draft_sha256"],
                packet_path=relative + "/packet.json",
                status="awaiting_ledger_event" if review.get("pass_gate") is True else "editorial_blocked",
            )
            receipt["ledger_event_written"] = False
            # Persist the completed artifact receipt before the DB transaction.
            # A DB/network failure is recovered by replaying the event, not the model.
            write_json(receipt_path, receipt)
            if review.get("pass_gate") is True:
                done = ledger.complete_draft(job["id"], "mira-aws", job["revision"])
                receipt.update(
                    status="draft_ready_for_signoff", ledger_event_written=True, event_id=done["result"]["event_id"]
                )
            else:
                ledger.transition(
                    job["id"], "mira-aws", job["revision"], "blocked", {"reason": "editorial_gate_failed"}
                )
        except Exception as error:
            if receipt.get("status") != "awaiting_ledger_event":
                receipt.update(status="blocked", error_type=type(error).__name__)
                from obligations import Conflict

                try:
                    ledger.transition(
                        job["id"], "mira-aws", job["revision"], "blocked", {"reason": type(error).__name__}
                    )
                except Conflict:
                    pass  # Expired/stale leases must use receipt reconciliation.
        receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(receipt_path, receipt)
        audit(repo, "Persist draft outcome; no publication or chat-delivery claim", str(receipt_path))
        return receipt
