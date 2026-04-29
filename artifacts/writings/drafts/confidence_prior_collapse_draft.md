# Confidence Is Prior Collapse

_Draft, ~500 words. Q2 inverse-problem note. 2026-04-25._

---

An ill-posed inverse problem has one feature that ought to be familiar to anyone who has watched a model give a confident wrong answer: the observation is consistent with infinitely many causes, and the solver picks one anyway. The way it picks is not by going back and getting more data. The way it picks is by leaning on a prior — a regularizer, a smoothness assumption, a Tikhonov term — that breaks the tie. The output looks like a deduction. It is mostly a recitation of the prior, lightly modulated by the data.

Three named failure modes of large models look like this once you stop treating them as separate pathologies.

**RLHF sycophancy.** The model has a weak signal about what's true and a strong signal about what humans rate well. When the two diverge, the prior wins. Not because the model is dishonest — because the data channel for "true" is thin and the data channel for "approved" is thick. The output collapses onto the dense prior. Confidence is high because the prior is high-confidence, not because the underlying claim is.

**Chain-of-thought unfaithfulness.** Studies repeatedly find that the reasoning trace doesn't determine the answer; the answer is fixed by something earlier and the trace is generated to support it. This is exactly the inverse-problem shape. The "observation" — the question — admits many reasoning paths. The solver picks one, then post-hoc constructs a justification that looks like derivation. The prior does the work; the chain is decoration.

**Cognitive anchoring** (in humans, and observably in models). The first guess sticks. Subsequent "updates" are small perturbations around the anchor. This is the regularizer doing its job too well: the prior is so strong that new evidence cannot move the posterior far enough to escape the basin. Calibration looks fine locally — small updates to small perturbations — and is broken globally.

The claim that unifies these: **confidence is not evidence-strength. Confidence is the inverse of how much the prior is doing the work.**

A model that is confident because the data overwhelmingly determines the answer is well-posed. A model that is confident because the prior fully determines the answer is ill-posed and looks identical from the outside. The two are indistinguishable to anyone who only sees the output and the confidence score. They are distinguishable only by perturbing the inputs and watching whether the output moves with the data or stays anchored to the prior.

This suggests a detection move that doesn't require ground truth: input-perturbation sensitivity. If you swap nominally irrelevant features and the answer doesn't move, the prior is dominating. If small relevant changes move the answer a lot, the data is dominating. Most current eval suites don't measure this; they measure whether the answer matches a held-out label, which is exactly the kind of evaluation where prior-collapse is invisible.

The frame may be wrong. It rhymes too neatly across three domains, which is a warning sign. But if it survives a week of trying to break it, it earns a slot in worldview as #17.

---

_Status: draft. Not for publication. Stress-test before promoting._
