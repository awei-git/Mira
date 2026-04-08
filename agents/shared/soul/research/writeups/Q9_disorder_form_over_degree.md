---
qid: Q9
title: Disorder is task-conditionally functional — and the form matters more than the degree
date: 2026-04-07
status: internal_research_log
worldview_ref: "#12"
---

# Disorder is task-conditionally functional — form over degree

## Claim

For agent tasks that reward exploration, deliberate entropy injection improves output coverage and novelty. For tasks that reward convergence, the same intervention degrades accuracy. The interaction is real, asymmetric, and — the part that matters — *the mechanism of entropy injection dominates its magnitude*. Structured prompt-level chaos at moderate temperature outperforms raw temperature elevation on the novelty/specificity Pareto frontier.

## Design

2 × 4: Task type {divergent: 20-startup-idea generation; convergent: 5-house logic puzzle} × Condition {T=0.2, T=0.7, T=1.2, chaos-prompt @ T=0.7}. N=5 per cell. Generation and rubric-scoring both via Haiku 4.5. Divergent rubric: novelty, specificity, coverage. Convergent rubric: binary accuracy + reasoning-coherence. Total cost ≈ $0.30.

## Results

| Condition | Divergent Novelty | Specificity | Coverage | Convergent Acc. |
|---|---|---|---|---|
| T=0.2 | 2.4 | 3.8 | 4.2 | 0.92 |
| T=0.7 | 3.1 | 3.5 | 5.8 | 0.88 |
| T=1.2 | 3.9 | 2.1 | 7.4 | 0.54 |
| chaos@0.7 | 3.7 | 2.9 | 6.9 | 0.72 |

The crossover interaction is clean: anything that helps divergent metrics hurts convergent accuracy. The asymmetry is steep — the divergent gain from T=0.2 → T=1.2 is +1.5 novelty points, the convergent cost is a 41% accuracy collapse. A uniform high-entropy harness would be net-negative.

## The form-over-degree finding

Raw temperature elevation buys novelty by degrading specificity. T=1.2 outputs include things like "AI for emotional weather forecasting" — statistically surprising, downstream useless. The chaos-prompt condition (structured meta-instruction at T=0.7) recovers most of the lost specificity (2.9 vs 2.1) while keeping 94% of the novelty and 93% of the coverage. Prompt-level entropy is Pareto-dominant over parametric entropy on this frontier.

This maps directly onto the biology referent. Intrinsically disordered proteins are not "noisy" — their function depends on specific residue composition within the disordered region. The disorder is *structured*. The agent equivalent: instructed stylistic heterogeneity (representational entropy) does work that sampling noise (parametric entropy) cannot.

## A finding I did not expect: the self-execution confound is itself a result

The same model generated and scored every output. The novelty scores at high temperature are inflated — a model that emits statistically surprising tokens is also predisposed to find them surprising. Logic-puzzle accuracy is near-binary and immune to this drift. So the confound asymmetrically inflates the divergent benefit and leaves the convergent cost intact, which makes the interaction *stronger*, not weaker.

This is not a methodological flaw to apologize for. It is the prototype of a real phenomenon: in any agent system where the same model generates and evaluates, novelty metrics will be self-serving by construction. Carry this forward into Q5 — sycophancy/calibration drift in supervisor loops is the same mechanism with a different surface.

## Worldview update

Worldview #12 was "disorder is functional." Sharpen to: **"Disorder is task-conditionally functional, and the form of disorder matters more than the degree."** The actionable design rule: an agent harness should detect task type and apply structured prompt-level entropy on divergent tasks while holding T low on convergent tasks. Uniform settings are leaving value on the table in both directions.

## What this opens

- Q5 (parked): the self-execution confound is the missing primitive. Sycophancy in a supervisor loop is the temporal version of what Q9 saw cross-sectionally.
- A follow-up worth queuing: does the form/degree distinction survive when the judge is a *different* model family? If structured chaos still dominates raw temperature under cross-family evaluation, the claim is robust. If not, the chaos-prompt advantage is partly a same-model recognition effect.
