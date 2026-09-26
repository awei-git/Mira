"""Isolated filesystem/contract checks. No real model, publication or AWS calls."""

import importlib.util
import json
from pathlib import Path
import sys

import pytest

from content_worker.files import active_snapshot, checksum, safe_file
from content_worker.identity import IDENTITY_FILES, load_identity
from content_worker.outbox import daily_records, write_outbox
from content_worker.seeds import POLICY_FILES, read_seeds, run_batch, run_seed

ROOT = Path(__file__).resolve().parents[1]


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


syncer = module_at("shared_sync_test", ROOT / "deploy/sync_shared.py")
dist_filter = module_at("dist_filter_test", ROOT / "deploy/dist_filter.py")


@pytest.fixture
def source(tmp_path):
    source = tmp_path / "source"
    projection = source / "identity/cloud"
    projection.mkdir(parents=True)
    hashes = {}
    for name in IDENTITY_FILES:
        body = ("Public content preferences for " + name).encode()
        (projection / name).write_bytes(body)
        hashes[name] = checksum(body)
    (projection / "manifest.json").write_text(
        json.dumps(
            {
                "scope": "content_only",
                "approved_by": "mira-app",
                "sha256": hashes,
            }
        )
    )
    (source / "identity/USER.md").write_text("PRIVATE SOURCE MUST NOT SHIP")
    (source / "skills").mkdir()
    return source


def test_raw_identity_is_not_a_cloud_input(tmp_path):
    raw = tmp_path / "raw"
    (raw / "identity").mkdir(parents=True)
    (raw / "identity/USER.md").write_text("private")
    target = tmp_path / "cloud"
    with pytest.raises(ValueError, match="projection_required"):
        syncer.sync(raw, target)
    assert not target.exists()


def test_sync_maps_identity_and_retains_legacy_bytes(source, tmp_path):
    old = tmp_path / "soul"
    old.mkdir()
    original = b"old private identity"
    (old / "identity.md").write_bytes(original)
    target = tmp_path / "shared"
    result = syncer.sync(source, target, activate=True, legacy_soul=old)
    assert result["activated"]
    assert next(old.glob("identity.md.legacy-*")).read_bytes() == original
    assert not (old / "identity.md").exists()
    soul = load_identity(target)
    assert "SOUL.md" in soul["identity"] and "IDENTITY.md" in soul["identity"]
    assert "USER.md" in soul["interests"]
    assert all("PRIVATE SOURCE" not in text for text in soul.values())
    (active_snapshot(target) / "identity/SOUL.md").write_text("tampered")
    with pytest.raises(ValueError, match="snapshot_file_mismatch"):
        load_identity(target)


def test_activation_failure_restores_pointer_and_legacy(source, tmp_path, monkeypatch):
    target, old = tmp_path / "shared", tmp_path / "old"
    target.mkdir()
    old.mkdir()
    (old / "memory.md").write_text("retain me")
    previous = b'{"snapshot":"previous-generation"}'
    (target / "current.json").write_bytes(previous)

    def fail(_):
        raise ValueError("injected_validation_failure")

    monkeypatch.setattr(syncer, "active_snapshot", fail)
    with pytest.raises(ValueError, match="injected"):
        syncer.sync(source, target, activate=True, legacy_soul=old)
    assert (target / "current.json").read_bytes() == previous
    assert (old / "memory.md").read_text() == "retain me"
    assert not list(old.glob("*.legacy-*"))


def test_sanitized_bundle_can_stage_on_both_targets(source, tmp_path):
    exported = syncer.sync(source, tmp_path / "export")
    assert not (source / "logs").exists()
    assert (tmp_path / "export/logs/permacomputing_audit.md").is_file()
    for host in ("old", "new"):
        destination = tmp_path / host
        result = syncer.install_snapshot(exported["path"], destination, audit_root=tmp_path / "audit")
        assert result["snapshot"] == exported["snapshot"]
        assert result["activated"] is False
        assert not (destination / "current.json").exists()
        assert (Path(result["path"]) / "identity/USER.md").read_text().startswith("Public content")
    assert not (source / "logs").exists()
    assert (tmp_path / "audit/logs/permacomputing_audit.md").is_file()


def test_transport_corruption_rejected_before_target_write(source, tmp_path):
    exported = syncer.sync(source, tmp_path / "export")
    (Path(exported["path"]) / "identity/SOUL.md").write_text("tampered")
    with pytest.raises(ValueError, match="transport_file_mismatch"):
        syncer.install_snapshot(exported["path"], tmp_path / "target", audit_root=source)
    assert not (tmp_path / "target").exists()


def test_missing_activation_argument_has_no_side_effect(source, tmp_path):
    target = tmp_path / "shared"
    with pytest.raises(ValueError, match="legacy_soul"):
        syncer.sync(source, target, activate=True)
    assert not target.exists()


def test_projection_hash_rejected_before_writes(source, tmp_path):
    (source / "identity/cloud/USER.md").write_text("unreviewed change")
    with pytest.raises(ValueError, match="hash_or_size"):
        syncer.sync(source, tmp_path / "shared")
    assert not (tmp_path / "shared").exists()


@pytest.mark.parametrize("path", ["../outside", "/absolute"])
def test_artifact_path_boundary(tmp_path, path):
    with pytest.raises(ValueError):
        safe_file(tmp_path, path)


def test_symlink_rejected(source, tmp_path):
    link = source / "identity/cloud/SOUL.md"
    link.unlink()
    link.symlink_to(source / "identity/USER.md")
    with pytest.raises(ValueError, match="symlink"):
        syncer.sync(source, tmp_path / "shared")


def test_skill_audit_failure_precedes_asset_writes(source, tmp_path, monkeypatch):
    # Mock package reads so tests do not save or enable a synthetic skill.
    folder = source / "skills/example"
    folder.mkdir()
    fake_path = folder / "SKILL.md"
    original_rglob = Path.rglob
    original_is_file = Path.is_file
    original_read = Path.read_bytes
    monkeypatch.setattr(Path, "rglob", lambda p, q: [fake_path] if p == folder else original_rglob(p, q))
    monkeypatch.setattr(Path, "is_file", lambda p: True if p == fake_path else original_is_file(p))
    monkeypatch.setattr(Path, "read_bytes", lambda p: b"audit test package" if p == fake_path else original_read(p))
    calls = []

    def reject(name, body):
        calls.append((name, body))
        return {"passed": False, "findings": ["blocked"]}

    with pytest.raises(ValueError, match="audit_blocked"):
        syncer.sync(source, tmp_path / "shared", audit_skill=reject)
    assert calls and "SKILL.md" in calls[0][1]
    assert not (tmp_path / "shared").exists()


@pytest.fixture
def content_repo(tmp_path, monkeypatch):
    repo = tmp_path / "content"
    monkeypatch.setenv("MIRA_LEDGER_PATH", str(tmp_path / "ledger/store.sqlite3"))
    (repo / "seeds").mkdir(parents=True)
    (repo / "seeds/seeds.jsonl").write_text("")
    for name in POLICY_FILES:
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("Editorial policy fixture")
    return repo


def ready(identifier="seed-a"):
    return {"seed_id": identifier, "status": "ready", "track": "substack_en"}


def successful_writer(workspace, *args):
    (workspace / "output.md").write_text("# A draft\n\n> A promise\n\nDraft fixture.")
    return "completed"


def successful_review(workspace, *args):
    return (workspace / "output.md").read_text(), {"pass_gate": True, "verifier": "test_only"}


def test_seed_filter_and_duplicate_rejection(content_repo):
    for row in ({"seed_id": "cn", "track": "zh", "status": "ready"}, {"seed_id": "candidate", "status": "candidate"}):
        assert run_seed(content_repo, row)["status"] == "ineligible"
    assert not (content_repo / "data").exists()
    p = content_repo / "seeds.jsonl"
    p.write_text(json.dumps(ready()) + "\n" + json.dumps(ready()))
    with pytest.raises(ValueError, match="duplicate"):
        read_seeds(p)
    with pytest.raises(ValueError, match="invalid_seed_id"):
        run_seed(content_repo, ready("../escape"))


def test_draft_receipt_is_idempotent_and_never_claims_event(content_repo):
    calls = []

    def writer(*args):
        calls.append(1)
        return successful_writer(*args)

    first = run_seed(content_repo, ready(), writer=writer, reviewer=successful_review)
    again = run_seed(content_repo, ready(), writer=writer, reviewer=successful_review)
    assert first == again and len(calls) == 1
    assert first["status"] == "draft_ready_for_signoff"
    assert first["ledger_event_written"] is True and first["published"] is False
    artifact = content_repo / first["draft_path"]
    assert artifact.name == "seed-a-draft.md"
    artifact.write_text("changed after receipt")
    with pytest.raises(ValueError, match="artifact_changed"):
        run_seed(content_repo, ready(), writer=writer, reviewer=successful_review)


def test_batch_advances_past_completed_seed(content_repo):
    options = {"writer": successful_writer, "reviewer": successful_review}
    rows = [ready("a"), ready("b")]
    assert run_batch(content_repo, rows, **options)[0]["seed_id"] == "a"
    assert run_batch(content_repo, rows, **options)[0]["seed_id"] == "b"
    assert run_batch(content_repo, rows, **options) == []


def test_failed_paid_work_is_not_retried(content_repo):
    calls = []

    def fail(*_):
        calls.append(1)
        raise RuntimeError("private provider error must not leak")

    first = run_seed(content_repo, ready(), writer=fail)
    assert first["status"] == "blocked"
    assert "private provider" not in json.dumps(first)
    assert run_batch(content_repo, [ready()], writer=fail) == []
    assert len(calls) == 1


def test_missing_identity_does_not_reserve_seed(content_repo, monkeypatch):
    monkeypatch.delenv("MIRA_SHARED_ROOT", raising=False)
    with pytest.raises(ValueError, match="requires_shared_identity"):
        run_seed(content_repo, ready())
    assert not (content_repo / "data").exists()


def test_outbox_three_sections_and_evidence(content_repo):
    records = {"journal": [{"text": "An unverified idea."}], "verified_learnings": [], "sync": []}
    out = write_outbox(content_repo, "2026-09-23", records)
    assert [s for s in out.read_text().splitlines() if s.startswith("## ")] == [
        "## journal",
        "## verified learnings",
        "## 需同步事项",
    ]
    before = out.stat().st_mtime_ns
    write_outbox(content_repo, "2026-09-23", records)
    assert out.stat().st_mtime_ns == before
    records["verified_learnings"] = [{"text": "Self-assessment"}]
    with pytest.raises(ValueError, match="independent_verification"):
        write_outbox(content_repo, "2026-09-23", records)
    assert "Self-assessment" not in out.read_text()


def test_distribution_excludes_before_upload():
    cfg = {"data_dirs": ["data", "logs"], "package_excludes": ["identity", "skills"]}
    for name in ("identity/USER.md", "identity/cloud/USER.md", "data/health.json", "skills/x/SKILL.md"):
        assert not dist_filter.include_file(name, cfg)
    assert dist_filter.include_file("lib/content_worker/seeds.py", cfg)
    assert dist_filter.include_file("seeds/seeds.jsonl", cfg)
    with pytest.raises(ValueError):
        dist_filter.include_file("../escape", cfg)


def test_outbox_uses_new_york_date_and_does_not_self_verify(content_repo):
    receipt = content_repo / "data/drafts/substack_en/a/version/receipt.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"seed_id": "a", "status": "running", "started_at": "2026-09-24T02:00:00+00:00"}))
    day = daily_records(content_repo, "2026-09-23")
    assert len(day["journal"]) == 1
    assert day["verified_learnings"] == []
    assert daily_records(content_repo, "2026-09-24")["journal"] == []


@pytest.mark.parametrize(
    "status, expected",
    [
        ("running", "reconcile its receipt"),
        ("blocked", "operator review"),
        ("editorial_blocked", "revision is required"),
        ("awaiting_ledger_contract", "chat return are pending"),
        ("unexpected", "Unknown receipt status"),
    ],
)
def test_outbox_explains_actual_producer_statuses(content_repo, status, expected):
    receipt = content_repo / "data/drafts/substack_en/a/version/receipt.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"seed_id": "a", "status": status, "started_at": "2026-09-23T12:00:00+00:00"}))
    records = daily_records(content_repo, "2026-09-23")
    assert expected in records["sync"][0]["text"]
    assert records["verified_learnings"] == []


def test_existing_handler_content_route_excludes_thread_recall(monkeypatch, tmp_path):
    import pathsetup  # noqa: F401
    from agents.writer import handler
    from content_worker import context

    monkeypatch.setattr(handler, "_surface_obsession_constraint_candidates", lambda *args: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("private context or quick branch reached")

    monkeypatch.setattr(handler, "build_runtime_context", forbidden)
    monkeypatch.setattr(handler, "_handle_quick_write", forbidden)
    monkeypatch.setattr(
        context, "writer_context", lambda: context.ContentContext(context.ContentPersona("content identity"))
    )
    seen = {}

    def full(workspace, task_id, content, title, bundle, **kwargs):
        seen.update(bundle=bundle, options=kwargs)
        return "fixture completed"

    monkeypatch.setattr(handler, "_handle_full_write", full)
    handler.handle(
        tmp_path,
        "seed-x",
        "Write a short note",
        "mira-app",
        "",
        content_only=True,
        thread_history="PRIVATE",
        thread_memory="PRIVATE",
    )
    assert seen["options"]["content_only"] is True
    assert seen["bundle"].thread_history == "" and seen["bundle"].thread_memory == ""


def test_pipeline_rejects_missing_content_identity_before_paid_call(tmp_path):
    import pathsetup  # noqa: F401
    from writing_workflow import run_full_pipeline

    with pytest.raises(ValueError, match="explicit_persona"):
        run_full_pipeline("title", "body", content_only=True, workspace=tmp_path / "draft")
    assert not (tmp_path / "draft").exists()


def test_usage_does_not_change_audited_skill_index(tmp_path, monkeypatch):
    from memory import soul_skills

    index = tmp_path / "index.json"
    index.write_text('[{"name":"example"}]')
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("MIRA_SHARED_ROOT", str(tmp_path / "shared"))
    monkeypatch.setattr(soul_skills, "SKILLS_INDEX", index)
    monkeypatch.setattr(soul_skills, "SOUL_DIR", runtime)
    before = index.read_bytes()
    soul_skills._update_skill_invocation("example")
    assert index.read_bytes() == before
    assert json.loads((runtime / "shared_skill_usage.json").read_text())[0]["use_count"] == 1


def test_api_packaging_skips_private_blob_download(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "deploy"))
    mkdist = module_at("mkdist_test", ROOT / "deploy/mkdist.py")
    tree = {
        "tree": [
            {"type": "blob", "path": "identity/USER.md", "sha": "private", "mode": "100644"},
            {"type": "blob", "path": "lib/code.py", "sha": "code", "mode": "100644"},
            {"type": "blob", "path": "link.py", "sha": "link", "mode": "120000"},
        ]
    }
    monkeypatch.setattr(mkdist, "api", lambda path: tree)
    fetched = []

    def blob(repo, sha):
        fetched.append(sha)
        return b"code fixture"

    monkeypatch.setattr(mkdist, "get_blob", blob)
    files = mkdist.build_from_api("example/repo", "sha", ".", {"package_excludes": ["identity"]})
    assert files == {"lib/code.py": b"code fixture"}
    assert fetched == ["code"]
    tree["truncated"] = True
    with pytest.raises(RuntimeError, match="incomplete_github_tree"):
        mkdist.build_from_api("example/repo", "sha", ".")


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("changed", [False, True])
def test_skill_hash_preserves_app_contract_and_cloud_integrity(tmp_path, monkeypatch, shared, changed):
    """Exercise the real loader with virtual files; never save/enable a test skill."""
    from datetime import datetime, timezone
    from memory import soul_skills

    if shared:
        monkeypatch.setenv("MIRA_SHARED_ROOT", str(tmp_path / "shared"))
    else:
        monkeypatch.delenv("MIRA_SHARED_ROOT", raising=False)
    index = tmp_path / "index.json"
    skill = tmp_path / "writing-example.md"
    raw = "\n  Evidence before assertions.  \n"
    entry = {
        "name": "writing-example",
        "tags": ["writing"],
        "audited_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    files = {index: json.dumps([entry]), skill: raw + ("Changed instructions." if changed else "")}
    read_text, exists = Path.read_text, Path.exists
    monkeypatch.setattr(Path, "read_text", lambda p, *a, **kw: files[p] if p in files else read_text(p, *a, **kw))
    monkeypatch.setattr(Path, "exists", lambda p: p in files or exists(p))
    monkeypatch.setattr(soul_skills, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(soul_skills, "SKILLS_INDEX", index)
    expected = checksum((raw if shared else raw.strip()).encode())
    monkeypatch.setattr(soul_skills, "_load_skill_audit_hashes", lambda: {"writing-example": expected})
    monkeypatch.setattr(soul_skills, "filter_superseded_skill_candidates", lambda rows, *a: rows)
    for name in (
        "warn_if_deprecated_skill_loaded",
        "_update_provenance_loaded",
        "_update_skill_invocation",
        "_warn_unverified_skill_efficacy",
    ):
        monkeypatch.setattr(soul_skills, name, lambda *a, **kw: None)
    audits = []

    def reject(name, text):
        audits.append(name)
        raise soul_skills.SkillAuditFailedError("test audit refuses modified bytes")

    monkeypatch.setattr(soul_skills, "audit_skill", reject)
    result = soul_skills.load_skills_for_task("writing", agent_type="writing")
    assert audits == (["writing-example"] if changed else [])
    assert result == ("" if changed else raw.strip())
