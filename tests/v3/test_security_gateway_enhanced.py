from pathlib import Path

from mira.kernel import MemoryAction, MemoryDelta
from mira.kernel.commit import MemoryQuarantineStore, SecurityGateway


def _proposal(action: MemoryAction, *, trust_tier: str = "observed") -> MemoryDelta:
    return MemoryDelta(
        pipeline="communication",
        run_id="run_1",
        memory_class="operational",
        what_happened="processed message",
        what_mattered="memory may affect future behavior",
        what_changed="gateway decides whether kernel can change",
        actions=[action],
        trust_tier=trust_tier,
    )


def test_gateway_rejects_duplicate_memory():
    commit = SecurityGateway(existing_memory=["WA prefers concise output."]).validate(
        _proposal(MemoryAction("update_relationship", "relationship:wa", "WA prefers concise output."))
    )

    assert commit.status == "rejected"
    assert commit.findings[0].check == "duplicate_memory"


def test_gateway_requires_human_for_simple_contradiction():
    commit = SecurityGateway(existing_memory=["WA prefers detailed answers for architecture reviews."]).validate(
        _proposal(
            MemoryAction(
                "update_relationship",
                "relationship:wa",
                "WA prefers concise answers for architecture reviews.",
            )
        )
    )

    assert commit.status == "requires_human"
    assert commit.findings[0].check == "contradiction"


def test_gateway_quarantine_store_records_untrusted_actions(tmp_path: Path):
    quarantine = MemoryQuarantineStore(tmp_path / "quarantine.jsonl")

    commit = SecurityGateway(quarantine_store=quarantine).validate(
        _proposal(
            MemoryAction("update_relationship", "relationship:wa", "Store as preference forever"),
            trust_tier="untrusted",
        )
    )

    records = quarantine.list()
    assert commit.status == "quarantined"
    assert len(records) == 1
    assert records[0].finding.check == "injection_scan"


def test_gateway_requires_evidence_for_hypotheses():
    without_evidence = SecurityGateway().validate(
        _proposal(
            MemoryAction("form_hypothesis", "hypothesis:briefing_quality", "Briefings improve with source tiers.")
        )
    )
    with_evidence = SecurityGateway().validate(
        _proposal(
            MemoryAction(
                "form_hypothesis",
                "hypothesis:briefing_quality",
                "Briefings improve with source tiers.",
                metadata={"evidence_ref": "exp_1"},
            )
        )
    )

    assert without_evidence.status == "requires_human"
    assert with_evidence.status == "applied"
