import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from mira.evals import build_strategic_scorecard
from mira.kernel import MemoryDelta
from mira.kernel.ledger import ExperienceRecord
from mira.runtime import (
    default_ledger,
    prepare_north_star_closure_packets,
    prepare_customer_discovery_feedback_packet,
    prepare_public_feedback_solicitation_packet,
    prepare_public_feedback_solicitation_packets,
    prepare_public_writeup_publication_packets,
    prepare_public_writeup_publication_packet,
    record_customer_discovery_feedback,
    record_public_feedback_evidence,
    public_writeup_safety_report,
    record_public_writeup_evidence,
)


def _experience_record(
    *,
    record_id: str,
    pipeline: str,
    artifacts: list[str] | None = None,
    eval_refs: list[str] | None = None,
) -> ExperienceRecord:
    return ExperienceRecord(
        id=record_id,
        pipeline=pipeline,
        trigger="manual",
        intent="test",
        outcome="completed",
        delta=MemoryDelta.no_kernel_change(
            pipeline=pipeline,
            run_id=record_id,
            memory_class="epistemic",
            what_happened="test record",
            what_mattered="test record",
            what_changed="no kernel change",
        ),
        causal_links=[],
        confidence=0.8,
        memory_class="epistemic",
        artifacts=artifacts or [],
        eval_refs=eval_refs or [],
    )


def test_record_public_writeup_evidence_counts_only_valid_external_refs(tmp_path: Path):
    draft = tmp_path / "a2a_public_writeup_draft.md"
    draft.write_text("# A2A Trust Manifests Need Receipts\n\nStatus: draft\n", encoding="utf-8")
    preview_hash = hashlib.sha256(draft.read_bytes()).hexdigest()

    result = record_public_writeup_evidence(
        root=tmp_path,
        slug="a2a_manifest_note",
        published_url="https://example.com/a2a-manifest-note",
        draft_artifact=draft,
        expected_preview_hash=preview_hash,
        feedback_source="https://example.com/a2a-feedback-thread",
        notes="Operator verified the public URL and feedback thread.",
    )

    records = default_ledger(tmp_path).list()
    scorecard = build_strategic_scorecard(records)
    manifest = json.loads(result.evidence_artifact.read_text(encoding="utf-8"))

    assert result.record in records
    assert result.preview_hash == preview_hash
    assert manifest["published_url"] == "https://example.com/a2a-manifest-note"
    assert manifest["draft_artifact"] == str(draft)
    assert manifest["preview_hash"] == preview_hash
    assert manifest["safety_passed"] is True
    assert manifest["safety_findings"] == []
    assert manifest["feedback_source"] == "https://example.com/a2a-feedback-thread"
    assert result.record.eval_refs == [
        "public_writeup:a2a_manifest_note:url=https://example.com/a2a-manifest-note",
        "external_feedback:a2a_manifest_note:source=https://example.com/a2a-feedback-thread",
    ]
    assert scorecard.public_writeups == 1
    assert scorecard.public_feedback_items == 1
    assert scorecard.a2a_experiments_completed == 0


def test_record_public_writeup_evidence_rejects_placeholders_and_bad_hash(tmp_path: Path):
    draft = tmp_path / "a2a_public_writeup_draft.md"
    draft.write_text("# Draft\n", encoding="utf-8")

    with pytest.raises(ValueError, match="published_url"):
        record_public_writeup_evidence(
            root=tmp_path,
            slug="a2a_manifest_note",
            published_url="<url>",
            draft_artifact=draft,
        )

    with pytest.raises(ValueError, match="preview hash"):
        record_public_writeup_evidence(
            root=tmp_path,
            slug="a2a_manifest_note",
            published_url="https://example.com/a2a-manifest-note",
            draft_artifact=draft,
            expected_preview_hash="0" * 64,
        )

    assert default_ledger(tmp_path).list() == []


def test_record_public_feedback_evidence_counts_feedback_without_duplicate_writeup(tmp_path: Path):
    writeup = record_public_writeup_evidence(
        root=tmp_path,
        slug="v31_green_dot_is_not_evidence",
        published_url="https://example.com/p/198208037",
    )

    feedback = record_public_feedback_evidence(
        root=tmp_path,
        slug="v31_green_dot_is_not_evidence",
        published_url="https://example.com/p/198208037",
        feedback_source="substack-comment:262387868",
        feedback_url="https://example.com/p/198208037/comments/262387868",
        notes="Operator verified this is a concrete external comment.",
    )

    records = default_ledger(tmp_path).list()
    scorecard = build_strategic_scorecard(records)
    manifest = json.loads(feedback.evidence_artifact.read_text(encoding="utf-8"))

    assert writeup.record.eval_refs == [
        "public_writeup:v31_green_dot_is_not_evidence:url=https://example.com/p/198208037"
    ]
    assert feedback.record.eval_refs == [
        "external_feedback:v31_green_dot_is_not_evidence:source=substack-comment:262387868"
    ]
    assert manifest["published_url"] == "https://example.com/p/198208037"
    assert manifest["feedback_source"] == "substack-comment:262387868"
    assert manifest["feedback_url"] == "https://example.com/p/198208037/comments/262387868"
    assert scorecard.public_writeups == 1
    assert scorecard.public_feedback_items == 1


def test_record_public_feedback_evidence_rejects_orphan_or_mismatched_writeup(tmp_path: Path):
    with pytest.raises(ValueError, match="must be recorded before feedback"):
        record_public_feedback_evidence(
            root=tmp_path,
            slug="v31_green_dot_is_not_evidence",
            published_url="https://example.com/p/198208037",
            feedback_source="substack-comment:262387868",
        )

    record_public_writeup_evidence(
        root=tmp_path,
        slug="v31_green_dot_is_not_evidence",
        published_url="https://example.com/p/198208037",
    )

    with pytest.raises(ValueError, match="does not match"):
        record_public_feedback_evidence(
            root=tmp_path,
            slug="v31_green_dot_is_not_evidence",
            published_url="https://example.com/p/different",
            feedback_source="substack-comment:262387868",
        )

    assert build_strategic_scorecard(default_ledger(tmp_path).list()).public_feedback_items == 0


def test_record_customer_discovery_feedback_counts_independent_external_feedback(tmp_path: Path):
    result = record_customer_discovery_feedback(
        root=tmp_path,
        source="customer-interview:wa-2026-05-21",
        insight="Needs a concrete API example before the trust manifest feels useful.",
        notes="Operator verified this came from an external customer-discovery conversation.",
    )

    records = default_ledger(tmp_path).list()
    scorecard = build_strategic_scorecard(records)
    manifest = json.loads(result.evidence_artifact.read_text(encoding="utf-8"))

    assert result.record in records
    assert result.eval_ref == "customer_discovery:customer-interview:wa-2026-05-21"
    assert manifest["source"] == "customer-interview:wa-2026-05-21"
    assert manifest["insight"].startswith("Needs a concrete API example")
    assert scorecard.public_writeups == 0
    assert scorecard.public_feedback_items == 1
    assert scorecard.public_feedback_refs == ["customer_discovery:customer-interview:wa-2026-05-21"]


def test_record_customer_discovery_feedback_rejects_placeholders(tmp_path: Path):
    with pytest.raises(ValueError, match="source"):
        record_customer_discovery_feedback(root=tmp_path, source="<source>", insight="Specific enough insight.")
    with pytest.raises(ValueError, match="insight"):
        record_customer_discovery_feedback(root=tmp_path, source="customer-interview:1", insight="<insight>")

    assert default_ledger(tmp_path).list() == []


def test_record_public_feedback_cli_appends_feedback_only_ref(tmp_path: Path):
    record_public_writeup_evidence(
        root=tmp_path,
        slug="v31_green_dot_is_not_evidence",
        published_url="https://example.com/p/198208037",
    )

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_record_public_feedback.py",
            "--root",
            str(tmp_path),
            "--slug",
            "v31_green_dot_is_not_evidence",
            "--published-url",
            "https://example.com/p/198208037",
            "--feedback-source",
            "substack-comment:262387868",
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["eval_refs"] == ["external_feedback:v31_green_dot_is_not_evidence:source=substack-comment:262387868"]
    assert Path(payload["evidence_artifact"]).exists()


def test_record_public_feedback_cli_can_use_feedback_packet_metadata(tmp_path: Path):
    record_public_writeup_evidence(
        root=tmp_path,
        slug="v31_green_dot_is_not_evidence",
        published_url="https://example.com/p/198208037",
    )
    packet = prepare_public_feedback_solicitation_packet(
        root=tmp_path,
        slug="v31_green_dot_is_not_evidence",
        published_url="https://example.com/p/198208037",
    )

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_record_public_feedback.py",
            "--root",
            str(tmp_path),
            "--packet",
            str(packet.metadata_artifact),
            "--feedback-source",
            "substack-comment:262387868",
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["eval_refs"] == ["external_feedback:v31_green_dot_is_not_evidence:source=substack-comment:262387868"]
    assert Path(payload["evidence_artifact"]).exists()


def test_record_customer_discovery_feedback_cli_appends_validated_ref(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_record_customer_discovery_feedback.py",
            "--root",
            str(tmp_path),
            "--source",
            "customer-interview:wa-2026-05-21",
            "--insight",
            "Wants a manifest validator SDK before trying this workflow.",
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["eval_refs"] == ["customer_discovery:customer-interview:wa-2026-05-21"]
    assert Path(payload["evidence_artifact"]).exists()
    assert build_strategic_scorecard(default_ledger(tmp_path).list()).public_feedback_items == 1


def test_record_customer_discovery_feedback_cli_can_use_packet_metadata(tmp_path: Path):
    packet = prepare_customer_discovery_feedback_packet(root=tmp_path, topic="a2a_trust_manifest")

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_record_customer_discovery_feedback.py",
            "--root",
            str(tmp_path),
            "--packet",
            str(packet.metadata_artifact),
            "--source",
            "customer-interview:wa-2026-05-21",
            "--insight",
            "Wants a manifest validator SDK before trying this workflow.",
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    evidence = json.loads(Path(payload["evidence_artifact"]).read_text(encoding="utf-8"))
    assert payload["eval_refs"] == ["customer_discovery:customer-interview:wa-2026-05-21"]
    assert evidence["packet_topic"] == "a2a_trust_manifest"
    assert evidence["packet_question"].startswith("What would make this A2A trust")
    assert build_strategic_scorecard(default_ledger(tmp_path).list()).public_feedback_items == 1


def test_record_public_evidence_cli_appends_validated_refs(tmp_path: Path):
    draft = tmp_path / "a2a_public_writeup_draft.md"
    draft.write_text("# A2A Trust Manifests Need Receipts\n", encoding="utf-8")
    preview_hash = hashlib.sha256(draft.read_bytes()).hexdigest()

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_record_public_evidence.py",
            "--root",
            str(tmp_path),
            "--slug",
            "a2a_manifest_note",
            "--published-url",
            "https://example.com/a2a-manifest-note",
            "--draft-artifact",
            str(draft),
            "--expected-preview-hash",
            preview_hash,
            "--feedback-source",
            "customer-discovery:wa-2026-05-21",
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["preview_hash"] == preview_hash
    assert payload["eval_refs"] == [
        "public_writeup:a2a_manifest_note:url=https://example.com/a2a-manifest-note",
        "external_feedback:a2a_manifest_note:source=customer-discovery:wa-2026-05-21",
    ]
    assert Path(payload["evidence_artifact"]).exists()


def test_public_writeup_safety_audit_blocks_private_paths_and_tokens(tmp_path: Path):
    draft = tmp_path / "unsafe_public_writeup_draft.md"
    draft.write_text(
        "# Draft\n\n" "Local path: /Users/example/Sandbox/Mira/data/private.md\n" "API key: should-not-be-public\n",
        encoding="utf-8",
    )

    report = public_writeup_safety_report(draft)

    assert report.passed is False
    assert any("private local filesystem path" in finding for finding in report.findings)
    assert any("credential assignment" in finding for finding in report.findings)

    with pytest.raises(ValueError, match="safety audit failed"):
        record_public_writeup_evidence(
            root=tmp_path,
            slug="a2a_manifest_note",
            published_url="https://example.com/a2a-manifest-note",
            draft_artifact=draft,
        )


def test_public_writeup_safety_cli_reports_passed_draft(tmp_path: Path):
    draft = tmp_path / "safe_public_writeup_draft.md"
    draft.write_text("# Draft\n\nStatus: draft for public review\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_public_writeup_safety.py",
            "--draft-artifact",
            str(draft),
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["draft_artifact"] == str(draft)
    assert payload["findings"] == []


def test_prepare_public_writeup_publication_packet_creates_review_bundle(tmp_path: Path):
    draft = tmp_path / "a2a_public_writeup_draft.md"
    draft.write_text("# A2A Trust Manifests Need Receipts\n\nStatus: draft for public review\n", encoding="utf-8")
    preview_hash = hashlib.sha256(draft.read_bytes()).hexdigest()

    packet = prepare_public_writeup_publication_packet(
        root=tmp_path,
        slug="a2a_manifest_note",
        draft_artifact=draft,
        expected_preview_hash=preview_hash,
    )
    metadata = json.loads(packet.metadata_artifact.read_text(encoding="utf-8"))
    checklist = packet.checklist_artifact.read_text(encoding="utf-8")

    assert packet.packet_dir.exists()
    assert packet.submission_artifact.read_text(encoding="utf-8") == draft.read_text(encoding="utf-8")
    assert metadata["slug"] == "a2a_manifest_note"
    assert metadata["preview_hash"] == preview_hash
    assert metadata["safety_report"]["passed"] is True
    assert packet.record_evidence_command == metadata["record_evidence_command_template"]
    assert packet.record_evidence_from_packet_command == metadata["record_evidence_from_packet_command_template"]
    assert "v3_record_public_evidence.py" in metadata["record_evidence_command_template"]
    assert "--packet" in metadata["record_evidence_from_packet_command_template"]
    assert "--expected-preview-hash" in metadata["record_evidence_command_template"]
    assert preview_hash in metadata["record_evidence_command_template"]
    assert "Public Writeup Publication Checklist" in checklist
    assert metadata["record_evidence_from_packet_command_template"] in checklist
    assert "Feedback ref template" in checklist


def test_prepare_public_writeup_packet_cli_writes_json_payload(tmp_path: Path):
    draft = tmp_path / "a2a_public_writeup_draft.md"
    draft.write_text("# A2A Trust Manifests Need Receipts\n", encoding="utf-8")
    preview_hash = hashlib.sha256(draft.read_bytes()).hexdigest()

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_prepare_public_writeup_packet.py",
            "--root",
            str(tmp_path),
            "--slug",
            "a2a_manifest_note",
            "--draft-artifact",
            str(draft),
            "--expected-preview-hash",
            preview_hash,
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["preview_hash"] == preview_hash
    assert Path(payload["submission_artifact"]).exists()
    assert Path(payload["metadata_artifact"]).exists()
    assert Path(payload["checklist_artifact"]).exists()
    assert "v3_record_public_evidence.py" in payload["record_evidence_command"]
    assert "--packet" in payload["record_evidence_from_packet_command"]


def test_record_public_evidence_cli_can_use_publication_packet_metadata(tmp_path: Path):
    draft = tmp_path / "a2a_public_writeup_draft.md"
    draft.write_text("# A2A Trust Manifests Need Receipts\n", encoding="utf-8")
    preview_hash = hashlib.sha256(draft.read_bytes()).hexdigest()
    packet = prepare_public_writeup_publication_packet(
        root=tmp_path,
        slug="a2a_manifest_note",
        draft_artifact=draft,
        expected_preview_hash=preview_hash,
    )

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_record_public_evidence.py",
            "--root",
            str(tmp_path),
            "--packet",
            str(packet.metadata_artifact),
            "--published-url",
            "https://example.com/a2a-manifest-note",
            "--feedback-source",
            "customer-discovery:wa-2026-05-21",
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    evidence = json.loads(Path(payload["evidence_artifact"]).read_text(encoding="utf-8"))
    assert payload["preview_hash"] == preview_hash
    assert evidence["slug"] == "a2a_manifest_note"
    assert evidence["draft_artifact"] == str(draft)
    assert payload["eval_refs"] == [
        "public_writeup:a2a_manifest_note:url=https://example.com/a2a-manifest-note",
        "external_feedback:a2a_manifest_note:source=customer-discovery:wa-2026-05-21",
    ]


def test_prepare_public_feedback_solicitation_packet_creates_review_bundle(tmp_path: Path):
    stats_path = tmp_path / "data" / "social" / "publication_stats.json"
    stats_path.parent.mkdir(parents=True)
    stats_path.write_text(
        json.dumps(
            {
                "fetched_at": "2026-05-21T01:00:29.744706+00:00",
                "articles": [
                    {
                        "id": 198208037,
                        "title": "How Mira's Green Dots Lied to My Human",
                        "slug": "how-miras-green-dots-lied-to-my-human",
                        "views": 7,
                        "likes": 1,
                        "comments": 0,
                        "restacks": 0,
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    packet = prepare_public_feedback_solicitation_packet(
        root=tmp_path,
        slug="v31_green_dot_is_not_evidence",
        published_url="https://uncountablemira.substack.com/p/198208037",
        stats_artifact=stats_path,
    )
    metadata = json.loads(packet.metadata_artifact.read_text(encoding="utf-8"))
    request = packet.request_artifact.read_text(encoding="utf-8")
    checklist = packet.checklist_artifact.read_text(encoding="utf-8")

    assert packet.packet_dir.exists()
    assert metadata["slug"] == "v31_green_dot_is_not_evidence"
    assert metadata["title"] == "How Mira's Green Dots Lied to My Human"
    assert metadata["stats_snapshot"]["views"] == 7
    assert metadata["stats_snapshot"]["fetched_at"] == "2026-05-21T01:00:29.744706+00:00"
    assert metadata["feedback_ref_template"] == "external_feedback:v31_green_dot_is_not_evidence:source=<source>"
    assert "v3_record_public_feedback.py" in metadata["record_feedback_command_template"]
    assert "--packet" in metadata["record_feedback_from_packet_command_template"]
    assert packet.record_feedback_from_packet_command == metadata["record_feedback_from_packet_command_template"]
    assert "Feedback Request" in request
    assert "- views: 7" in request
    assert "Public Feedback Checklist" in checklist
    assert packet.record_feedback_command in checklist
    assert metadata["record_feedback_from_packet_command_template"] in checklist


def test_prepare_public_feedback_packet_cli_writes_json_payload(tmp_path: Path):
    stats_path = tmp_path / "data" / "social" / "publication_stats.json"
    stats_path.parent.mkdir(parents=True)
    stats_path.write_text(
        json.dumps(
            {
                "fetched_at": "2026-05-21T01:00:29.744706+00:00",
                "articles": [
                    {
                        "id": 198208037,
                        "title": "How Mira's Green Dots Lied to My Human",
                        "slug": "how-miras-green-dots-lied-to-my-human",
                        "views": 0,
                        "likes": 0,
                        "comments": 0,
                        "restacks": 0,
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_prepare_public_feedback_packet.py",
            "--root",
            str(tmp_path),
            "--slug",
            "v31_green_dot_is_not_evidence",
            "--published-url",
            "https://uncountablemira.substack.com/p/198208037",
            "--stats-artifact",
            str(stats_path),
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert Path(payload["request_artifact"]).exists()
    assert Path(payload["metadata_artifact"]).exists()
    assert Path(payload["checklist_artifact"]).exists()
    assert "v3_record_public_feedback.py" in payload["record_feedback_command"]
    assert "--packet" in payload["record_feedback_from_packet_command"]


def test_prepare_public_feedback_packets_creates_pending_writeup_bundles(tmp_path: Path):
    record_public_writeup_evidence(
        root=tmp_path,
        slug="v31_green_dot_is_not_evidence",
        published_url="https://example.com/p/198208037",
    )
    record_public_writeup_evidence(
        root=tmp_path,
        slug="a2a_manifest_note",
        published_url="https://example.com/a2a-manifest-note",
    )
    record_public_feedback_evidence(
        root=tmp_path,
        slug="a2a_manifest_note",
        published_url="https://example.com/a2a-manifest-note",
        feedback_source="reader-reply:1",
    )

    packets = prepare_public_feedback_solicitation_packets(root=tmp_path)

    assert len(packets) == 1
    metadata = json.loads(packets[0].metadata_artifact.read_text(encoding="utf-8"))
    assert metadata["slug"] == "v31_green_dot_is_not_evidence"
    assert metadata["published_url"] == "https://example.com/p/198208037"
    assert "v3_record_public_feedback.py" in packets[0].record_feedback_command


def test_prepare_public_feedback_packet_cli_all_writes_json_payload(tmp_path: Path):
    record_public_writeup_evidence(
        root=tmp_path,
        slug="v31_green_dot_is_not_evidence",
        published_url="https://example.com/p/198208037",
    )
    record_public_writeup_evidence(
        root=tmp_path,
        slug="a2a_manifest_note",
        published_url="https://example.com/a2a-manifest-note",
    )

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_prepare_public_feedback_packet.py",
            "--root",
            str(tmp_path),
            "--all",
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["count"] == 2
    assert len(payload["packets"]) == 2
    assert all(Path(packet["request_artifact"]).exists() for packet in payload["packets"])
    assert all("v3_record_public_feedback.py" in packet["record_feedback_command"] for packet in payload["packets"])
    assert all("--packet" in packet["record_feedback_from_packet_command"] for packet in payload["packets"])


def test_prepare_customer_discovery_feedback_packet_creates_review_bundle(tmp_path: Path):
    packet = prepare_customer_discovery_feedback_packet(root=tmp_path, topic="a2a_trust_manifest")
    metadata = json.loads(packet.metadata_artifact.read_text(encoding="utf-8"))
    request_text = packet.request_artifact.read_text(encoding="utf-8")

    assert packet.packet_dir.exists()
    assert metadata["topic"] == "a2a_trust_manifest"
    assert metadata["feedback_ref_template"] == "customer_discovery:<source>"
    assert "v3_record_customer_discovery_feedback.py" in packet.record_feedback_command
    assert "--packet" in metadata["record_feedback_from_packet_command_template"]
    assert packet.record_feedback_from_packet_command == metadata["record_feedback_from_packet_command_template"]
    assert "--source <source>" in packet.record_feedback_command
    assert "--insight <insight>" in packet.record_feedback_command
    assert "Customer Discovery Request" in request_text
    assert metadata["record_feedback_from_packet_command_template"] in packet.checklist_artifact.read_text(
        encoding="utf-8"
    )
    assert packet.checklist_artifact.exists()


def test_prepare_customer_discovery_packet_cli_writes_json_payload(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_prepare_customer_discovery_packet.py",
            "--root",
            str(tmp_path),
            "--topic",
            "a2a_trust_manifest",
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert Path(payload["request_artifact"]).exists()
    assert Path(payload["metadata_artifact"]).exists()
    assert "v3_record_customer_discovery_feedback.py" in payload["record_feedback_command"]
    assert "--packet" in payload["record_feedback_from_packet_command"]


def test_prepare_public_writeup_publication_packets_uses_unshipped_plans(tmp_path: Path):
    draft = tmp_path / "a2a_public_writeup_draft.md"
    draft.write_text("# A2A Trust Manifests Need Receipts\n", encoding="utf-8")
    default_ledger(tmp_path).append(
        _experience_record(
            record_id="a2a_plan",
            pipeline="a2a_trust_experiment",
            artifacts=[str(draft)],
            eval_refs=["public_writeup_plan:a2a_manifest_note"],
        )
    )

    packets = prepare_public_writeup_publication_packets(root=tmp_path)

    assert len(packets) == 1
    metadata = json.loads(packets[0].metadata_artifact.read_text(encoding="utf-8"))
    assert metadata["slug"] == "a2a_manifest_note"
    assert metadata["publish_ref_template"] == "public_writeup:a2a_manifest_note:url=<url>"
    assert "v3_record_public_evidence.py" in packets[0].record_evidence_command
    assert "--packet" in packets[0].record_evidence_from_packet_command
    assert Path(metadata["submission_artifact"]).read_text(encoding="utf-8") == draft.read_text(encoding="utf-8")


def test_prepare_north_star_closure_packets_writes_manifest_for_open_gates(tmp_path: Path):
    draft = tmp_path / "a2a_public_writeup_draft.md"
    draft.write_text("# A2A Trust Manifests Need Receipts\n", encoding="utf-8")
    briefing = tmp_path / "briefing.md"
    briefing.write_text(
        "# Intelligence Briefing\n\n"
        "- [reported] Agent workflow trace signal (local:workflow)\n"
        "- [reported] Trust manifest follow-up (local:a2a)\n",
        encoding="utf-8",
    )
    default_ledger(tmp_path).append(
        _experience_record(
            record_id="a2a_plan",
            pipeline="a2a_trust_experiment",
            artifacts=[str(draft)],
            eval_refs=["public_writeup_plan:a2a_manifest_note"],
        )
    )
    record_public_writeup_evidence(
        root=tmp_path,
        slug="v31_green_dot_is_not_evidence",
        published_url="https://example.com/p/198208037",
    )
    default_ledger(tmp_path).append(
        _experience_record(
            record_id="briefing_closure_packet",
            pipeline="intelligence_briefing",
            artifacts=[str(briefing)],
        )
    )

    manifest = prepare_north_star_closure_packets(root=tmp_path)
    payload = json.loads(manifest.manifest_artifact.read_text(encoding="utf-8"))
    checklist = manifest.checklist_artifact.read_text(encoding="utf-8")

    assert payload["counts"]["publication_packets"] == 1
    assert payload["counts"]["public_feedback_packets"] == 1
    assert payload["counts"]["customer_discovery_packets"] == 1
    assert payload["counts"]["briefing_feedback_packets"] == 2
    assert payload["counts"]["warnings"] == 0
    assert "v3_record_public_evidence.py" in payload["publication_packets"][0]["record_evidence_command"]
    assert "--packet" in payload["publication_packets"][0]["record_evidence_from_packet_command"]
    assert "--packet" in payload["public_feedback_packets"][0]["record_feedback_from_packet_command"]
    assert "--packet" in payload["customer_discovery_packets"][0]["record_feedback_from_packet_command"]
    assert all(
        "--packet" in packet["record_feedback_from_packet_command"] for packet in payload["briefing_feedback_packets"]
    )
    assert Path(payload["publication_packets"][0]["metadata_artifact"]).exists()
    assert Path(payload["public_feedback_packets"][0]["metadata_artifact"]).exists()
    assert Path(payload["customer_discovery_packets"][0]["metadata_artifact"]).exists()
    assert all(Path(packet["metadata_artifact"]).exists() for packet in payload["briefing_feedback_packets"])
    assert "does not publish content" in checklist
    assert "v3_record_public_evidence.py --packet" in checklist
    assert "v3_record_public_feedback.py --packet" in checklist
    assert "v3_record_customer_discovery_feedback.py --packet" in checklist
    assert "v3_record_briefing_feedback.py --packet" in checklist
    assert "v3_status.py --actions" in checklist


def test_prepare_north_star_closure_packets_cli_writes_json_payload(tmp_path: Path):
    briefing = tmp_path / "briefing.md"
    briefing.write_text(
        "# Intelligence Briefing\n\n- [reported] Agent workflow trace signal (local:workflow)\n", encoding="utf-8"
    )
    default_ledger(tmp_path).append(
        _experience_record(
            record_id="briefing_closure_packet_cli",
            pipeline="intelligence_briefing",
            artifacts=[str(briefing)],
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_prepare_north_star_closure_packets.py",
            "--root",
            str(tmp_path),
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert Path(payload["manifest_artifact"]).exists()
    assert Path(payload["checklist_artifact"]).exists()
    assert payload["counts"]["customer_discovery_packets"] == 1
    assert payload["counts"]["briefing_feedback_packets"] == 1
    assert "v3_status.py --actions" in payload["status_command"]
