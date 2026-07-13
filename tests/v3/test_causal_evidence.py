from mira.kernel import (
    BehavioralEffect,
    CausalEvidenceLog,
    DecisionRecord,
    MemoryUseTrace,
    classify_causal_evidence,
    confirm_ablation_evidence,
)


def test_causal_evidence_reaches_l4_only_with_ablation_ref():
    trace = MemoryUseTrace(
        memory_id="scar:tts_minimax",
        run_id="run_1",
        pipeline="podcast_production",
        step="tts_route",
        retrieved=True,
        included=True,
        cited=True,
    )
    decision = DecisionRecord(
        run_id="run_1",
        pipeline="podcast_production",
        step="tts_route",
        decision="use fallback_tts",
        memory_trace_ids=[trace.trace_id],
    )
    effect = BehavioralEffect(
        memory_id="scar:tts_minimax",
        decision_id=decision.decision_id,
        effect_type="changed_tool",
        counterfactual="without this scar the pipeline would have used minimax",
    )

    l3 = classify_causal_evidence("scar:tts_minimax", [trace], [decision], [effect])
    l4 = classify_causal_evidence(
        "scar:tts_minimax",
        [trace],
        [decision],
        [effect],
        ablation_ref="eval:tts_ablation_2026_05_15",
    )

    assert l3.level == "L3"
    assert l4.level == "L4"
    assert l4.ablation_ref == "eval:tts_ablation_2026_05_15"


def test_causal_evidence_distinguishes_retrieved_from_effective():
    trace = MemoryUseTrace(
        memory_id="note:irrelevant",
        run_id="run_1",
        pipeline="communication",
        step="classify",
        retrieved=True,
        included=False,
        cited=False,
    )

    evidence = classify_causal_evidence("note:irrelevant", [trace], [], [])

    assert evidence.level == "L1"


def test_causal_evidence_log_persists_records(tmp_path):
    log = CausalEvidenceLog(tmp_path / "causal.jsonl")
    trace = MemoryUseTrace(
        memory_id="memory:1",
        run_id="run_1",
        pipeline="communication",
        step="execute",
        retrieved=True,
        included=True,
        cited=True,
    )
    decision = DecisionRecord(
        run_id="run_1",
        pipeline="communication",
        step="execute",
        decision="use concise reply",
        memory_trace_ids=[trace.trace_id],
    )
    effect = BehavioralEffect(
        memory_id="memory:1",
        decision_id=decision.decision_id,
        effect_type="changed_route",
        counterfactual="would have used default reply prefix",
    )
    evidence = classify_causal_evidence("memory:1", [trace], [decision], [effect])

    saved = log.append(evidence)

    assert log.get(saved.evidence_id).memory_id == "memory:1"
    assert log.list()[0].level == "L3"


def test_confirm_ablation_evidence_requires_changed_counterfactual():
    unchanged = confirm_ablation_evidence(
        memory_id="memory:1",
        run_id="run_1",
        pipeline="a2a_trust_experiment",
        normal_decision="use manifest validator",
        counterfactual_decision="use manifest validator",
        effect_ids=["effect:1"],
    )
    changed = confirm_ablation_evidence(
        memory_id="memory:1",
        run_id="run_2",
        pipeline="a2a_trust_experiment",
        normal_decision="use manifest validator",
        counterfactual_decision="ask baseline trust question",
        effect_ids=["effect:2"],
    )

    assert unchanged.level == "L3"
    assert unchanged.ablation_ref is None
    assert changed.level == "L4"
    assert changed.ablation_ref.startswith("ablation_")
    assert changed.effect_ids == ["effect:2"]
