from mira.kernel import BehavioralEffect, DecisionRecord, MemoryUseTrace, classify_causal_evidence


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
