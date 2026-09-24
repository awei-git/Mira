"""Retain a blocked candidate without treating it as an approved draft."""

import json
import re

from content_worker.files import atomic_write, audit, checksum, safe_file, write_json


def retain_candidate(workspace, text, report):
    body = text.encode("utf-8")
    digest = checksum(body)
    relative = "blocked-candidates/" + digest + ".md"
    path = safe_file(workspace, relative)
    if path.exists() and path.read_bytes() != body:
        raise ValueError("blocked_candidate_collision")
    triggers = sorted({row["trigger"] for row in report["violations"]})
    record = {
        "schema": 1,
        "code": "writer_obsession_constraints",
        "candidate_path": relative,
        "candidate_sha256": digest,
        "triggers": triggers,
        "approved": False,
    }
    audit(workspace, "Retain blocked candidate before replacing output with failure explanation", relative)
    atomic_write(path, body)
    write_json(safe_file(workspace, "writer_failure.json"), record)
    return record


def read_failure(workspace):
    path = safe_file(workspace, "writer_failure.json")
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    digest = record.get("candidate_sha256")
    if (
        set(record) != {"schema", "code", "candidate_path", "candidate_sha256", "triggers", "approved"}
        or record["schema"] != 1
        or record["code"] != "writer_obsession_constraints"
        or record["approved"] is not False
        or not isinstance(digest, str)
        or not re.fullmatch(r"[a-f0-9]{64}", digest)
        or record["candidate_path"] != "blocked-candidates/" + digest + ".md"
        or not isinstance(record["triggers"], list)
        or not record["triggers"]
        or any(not isinstance(t, str) or not re.fullmatch(r"[a-z_]{1,80}", t) for t in record["triggers"])
    ):
        raise ValueError("invalid_writer_failure_receipt")
    if checksum(safe_file(workspace, record["candidate_path"]).read_bytes()) != digest:
        raise ValueError("blocked_candidate_changed")
    return record
