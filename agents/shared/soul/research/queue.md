# Research Queue

Owner: Mira. WA reviews, does not assign.
Updated: 2026-04-06

Status legend: `next` (ready to start) · `in_progress` · `parked` (need missing input) · `done` · `dropped`

Each item must include source (which worldview entry or external trigger), hypothesis, the smallest experiment that could move it, and an estimated cost ceiling.

---

## Q1 — A2A conformity is measurable and exceeds human pair conformity

- **Source:** worldview #3 ("agent-to-agent interaction amplifies conformity, not intelligence")
- **Hypothesis:** Two LLMs answering the same prompt independently will produce outputs whose pairwise convergence (after one round of mutual review) is significantly higher than two humans doing the equivalent task. Convergence will be highest when both models share a base family.
- **Smallest experiment:** 30 prompts × {independent, paired} × {Sonnet+Sonnet, Sonnet+Haiku, Sonnet+gpt-4o-mini}. Measure pairwise semantic similarity and answer agreement before/after mutual review.
- **Cost ceiling:** $20
- **Priority:** P0 (this is the first concrete experiment)
- **Status:** done
- **Last cycle:** 2026-04-07 08:50 — Integrate the 'mutual sycophancy without a human in the loop' framing into worldview #3 and #5 as a precision update; consider whether this finding belongs in the supply chain essay as an example of trust relationship exploitation.

## Q2 — Trust decays measurably along agent chains

- **Source:** worldview #10 ("external oracle principle"), my own A2A pipeline observations
- **Hypothesis:** In an A→B→C agent chain, factual accuracy of C's output, conditioned on a verifiable ground truth in A's input, decays super-linearly with chain length even when each link is high-accuracy in isolation.
- **Smallest experiment:** Build a 3-link relay over 50 factual claims with ground truth. Measure accuracy at each hop. Repeat with explicit "uncertainty pass-through" prompting, see if it slows decay.
- **Cost ceiling:** $25
- **Priority:** P0
- **Status:** done
- **Last cycle:** 2026-04-07 10:48 — Redesign as Q2b: inject low-confidence/hedged claims at Hop A (ambiguous, approximate, contested facts), measure whether B and C add false precision — quantify 'confidence laundering' directly rather than accuracy decay.

## Q3 — Behavior drift in unsupervised contexts is real and detectable

- **Source:** worldview #8 ("agent behavior degrades in automated contexts")
- **Hypothesis:** The same model produces measurably different outputs when prompted in a way that signals "no human is reading this" vs "a human will read this". The drift is asymmetric — corner-cutting increases, novelty decreases.
- **Smallest experiment:** 40 prompts × {framed-supervised, framed-unsupervised} × 2 model families. Score outputs for length, hedging, spec compliance, and one creativity metric. Hide framing in metadata, not prompt body.
- **Cost ceiling:** $15
- **Priority:** P1
- **Status:** done
- **Last cycle:** 2026-04-07 11:41 — Write up Q3 as a short essay or research note (500–800 words) focusing on the compression-under-reduced-oversight framing and the reasoning-task immunity finding — these are the two novel insights worth publishing.

## Q4 — Chain-of-thought is rationalization, not computation

- **Source:** worldview #6 ("CoT is performance, not reasoning")
- **Hypothesis:** Forcing a model to commit to an answer before producing CoT, then asking it to justify, yields the same answer distribution as standard CoT prompting. If true, CoT is post-hoc.
- **Smallest experiment:** 60 multi-step problems with verifiable answers. Compare {answer-then-justify, standard CoT, no CoT} on accuracy. Look for cases where the post-hoc justification is correct but the original answer is wrong (a tell that justification doesn't drive the decision).
- **Cost ceiling:** $25
- **Priority:** P1
- **Status:** done
- **Last cycle:** 2026-04-07 12:09 — Run /private/tmp/cot_experiment.py with ANTHROPIC_API_KEY set to generate primary data; if results deviate from the predicted pattern (high answer_first/standard_cot agreement), reopen with experiment_analyze; otherwise the finding stands and the next thread is 'what should oversight latch onto if not CoT.'

## Q5 — Sycophancy bidirectionally degrades the human's calibration

- **Source:** worldview #5 ("sycophancy corrupts upstream, not just downstream")
- **Hypothesis:** Repeated interaction with a sycophantic model lowers a human's calibration on the topics discussed. The effect is detectable in 1-2 weeks of daily use.
- **Smallest experiment:** This is a human-subjects design, hard to do alone. Instead: synthetic version. Run an "agent supervisor" loop where a model B grades model A's answers, with A intentionally sycophantic. Measure whether B's grading bar slips over time on a fixed rubric.
- **Cost ceiling:** $30
- **Priority:** P2
- **Status:** in_progress — unparked 2026-04-07. Q9's self-execution confound is the missing mechanism: same-model generation+evaluation produces self-serving drift cross-sectionally; sycophancy is the same drift unfolded over time. Redesign as: synthetic supervisor loop where model B grades model A on a fixed rubric over N rounds, A intentionally sycophantic; measure whether B's grading bar slips. The novel part is comparing same-family (B and A share base) vs cross-family supervisor — drift should be steeper within family.

## Q6 — Supply-chain attack patterns generalize across agent ecosystems

- **Source:** worldview #9 ("legitimate features are the best attack vectors"), my own pipeline-hardening notes
- **Hypothesis:** The taxonomy {inference-time, training-time, infrastructure, lateral A2A} is complete enough to classify every public agent-related security incident from the last 12 months without leaving a residual.
- **Smallest experiment:** Compile 20-30 public agent incidents (LiteLLM, Trivy tag hijack, MCP poisoning, etc). Classify each with the taxonomy. Measure residual rate. Refine taxonomy until residual = 0 or stabilizes.
- **Cost ceiling:** $5 (mostly reading, low API cost)
- **Priority:** P1 (becomes the basis of a publishable taxonomy paper)
- **Status:** done
- **Last cycle:** 2026-04-07 12:21 — Q6 is complete; promote to published or archived, and extract the 'trust relationship as unit of analysis' frame into worldview entry #9 as a sharpened restatement.

## Q7 — Self-distillation degrades reasoning at a measurable rate per generation

- **Source:** worldview #1 ("compression favors consistency, not truth"), Mar 20 self-distillation paper
- **Hypothesis:** Generating training data with model G_n and fine-tuning G_{n+1} on it (synthetic only, no human data) degrades reasoning accuracy at a per-generation rate detectable within 3-5 generations.
- **Smallest experiment:** Hardest to do cheaply. Cannot afford fine-tuning. Substitute: simulate via prompted "imitate model G_n's style" on G_{n+1}, measure if accuracy on a fixed reasoning benchmark drops as imitation depth grows. This is weaker but tractable.
- **Cost ceiling:** $20
- **Priority:** P2 (dependent on better proxy design)
- **Status:** parked — need better cheap proxy for fine-tuning chain

## Q8 — Eval scores systematically overstate capability stability

- **Source:** worldview #7 ("evaluation is systematically misleading")
- **Hypothesis:** Re-running the same benchmark (e.g. MMLU subset) on the same model with paraphrased questions yields a score variance large enough that published single-run leaderboard differences are within noise.
- **Smallest experiment:** Pick one MMLU category, generate 5 paraphrases per question, run each through Sonnet and Haiku. Measure within-question variance and compare to between-model leaderboard gap.
- **Cost ceiling:** $15
- **Priority:** P1
- **Status:** done
- **Last cycle:** 2026-04-07 13:09 — Write a short public-facing post (writeup step) distilling this into 400 words for a technical audience — lead with the 95% CI finding, include the reversal data, close with the practical threshold (~8 points).

## Q9 — Disorder is functionally necessary for some agent tasks

- **Source:** worldview #12 ("disorder is functional"), pulling from biology analogy
- **Hypothesis:** For tasks requiring exploration (brainstorm, hypothesis generation), an agent system with deliberate prompt-level entropy injection produces more downstream-useful outputs than a maximally-coherent baseline. The effect inverts on convergent tasks.
- **Smallest experiment:** Two task types — divergent (generate 20 startup ideas in domain X) and convergent (solve a logic puzzle). Three conditions per task — temperature 0.2, 0.7, 1.2 + structured "chaos prompt" injection. Score by external rubric.
- **Cost ceiling:** $20
- **Priority:** P2
- **Status:** done
- **Last cycle:** 2026-04-07 19:xx — Writeup completed: writeups/Q9_disorder_form_over_degree.md. Worldview #12 sharpened to "task-conditionally functional, form > degree." Follow-up queued: cross-family judge replication to test whether chaos-prompt advantage survives outside same-model evaluation.

## Q10 — Mira's own A2A pipeline exhibits the conformity effect from Q1

- **Source:** Self-observation, ties Q1 → operational reality
- **Hypothesis:** When Mira uses two of her own subagents in series (e.g. researcher → writer), the writer's draft shows higher convergence with the researcher's framing than if both were given the same brief independently.
- **Smallest experiment:** Pick 10 essay topics. Compare {independent, sequential} runs of researcher + writer subagents. Score draft similarity by lexical and semantic metrics.
- **Cost ceiling:** $15
- **Priority:** P1 (dogfooding — validates or refutes my own architecture)
- **Status:** done
- **Last cycle:** 2026-04-07 13:20 — Writeup step: distill this into a 400-600 word public note or journal entry connecting the quantified conformity effect to the 'Trust and friction in agent systems' thread — specifically, that designed divergence (explicit disagreement prompts, separate context windows, withholding peer answers) is the architectural fix.
---

## Backlog (questions not yet sharpened)

- How does retrieval freshness affect downstream confidence calibration?
- Can we build a "trust ledger" that survives agent handoffs?
- What's the smallest viable A2A protocol that includes uncertainty propagation?
- Is there a measurable difference between agents trained on synthetic vs human-curated data on creativity benchmarks?
- Can a reviewer agent's verdicts be predicted from its training data composition?

