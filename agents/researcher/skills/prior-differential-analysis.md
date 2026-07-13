---
activation_trigger: "Apply when cross-model-research-synthesis surfaces a disagreement that synthesis cannot resolve — use this skill to diagnose whether the disagreement is terminological or structural."
---

# Prior Differential Analysis

**Tags:** research, synthesis, methodology, multi-source, conceptual-analysis, epistemics

## Core Principle

When two sources or models disagree about a concept, the disagreement lives at one of two layers: vocabulary (same concept, different words) or concept boundary (genuinely different definitions of what the concept includes). Conflating these layers produces false synthesis. This skill isolates which layer carries the variance and names the prior difference that explains it.

## Technique

**Step 1: Setup — identify sources and question**

- Select two sources or models with known distributional differences: temporal (different training cutoffs), domain (specialist vs. generalist), or institutional (academic vs. practitioner, Western vs. non-Western corpus).
- Formulate a single conceptual question that both sources can answer (e.g., "What is consciousness?", "What counts as a market failure?", "What does 'alignment' mean?").
- Collect parallel responses without cross-contamination — query independently, preserve full outputs.

**Step 2: Vocabulary cluster extraction**

- For each response, extract a frequency-ranked list of key terms and phrases used to describe the concept.
- Compute the vocabulary delta: terms that appear prominently in source A but not B, and vice versa.
- Flag synonyms and translation pairs (different words, same referent) — these are candidates for vocabulary-layer differences.
- Note: high-frequency unique terms do not automatically mean concept-boundary differences; they may reflect register, era, or disciplinary jargon.

**Step 3: Concept boundary claim extraction**

- For each response, extract explicit and implicit scope claims:
  - What does the source include within the concept? (necessary conditions, canonical examples, defining features)
  - What does the source explicitly or implicitly exclude? (non-examples, boundary cases ruled out)
  - What is the concept's relationship to neighboring concepts? (subset, superset, disjoint, overlapping)
- Represent each source's concept boundary as a structured inclusion/exclusion list.

**Step 4: Layer classification**

For each observed difference between the two sources, classify it:

- **Vocabulary-layer:** The two sources agree on what the concept covers, but use different surface lexicon. Test: Can you translate source A's terms into source B's terms and get the same extension? If yes, vocabulary-layer.
- **Concept-boundary-layer:** The two sources draw the concept's boundary at different places — what one includes, the other excludes. Test: Is there a case that source A's definition covers but source B's does not (or vice versa)? If yes, concept-boundary-layer.
- Mark ambiguous cases separately — do not force a classification when the evidence is insufficient.

**Step 5: Variance attribution and prior identification**

- Determine which layer carries the larger variance: are most differences terminological, or do the sources genuinely disagree about the concept's scope?
- For concept-boundary differences, name the prior that best explains the divergence:
  - **Temporal prior:** one source reflects an older or newer consensus on where to draw the boundary
  - **Domain prior:** specialist domains extend or restrict the concept for technical reasons
  - **Institutional prior:** training data from different communities encodes different gatekeeping norms
  - **Purpose prior:** the sources were built for different downstream tasks, which shaped their definitions
- Where possible, identify a minimal test case — a specific object or scenario — that falls inside one source's concept boundary and outside the other's. This is the clearest evidence of a concept-boundary difference.

## Output Format

```
PRIOR DIFFERENTIAL ANALYSIS
Sources: [A] vs [B]
Conceptual question: ...

VOCABULARY DELTA
  Terms unique to A: ...
  Terms unique to B: ...
  Likely synonyms: ...

CONCEPT BOUNDARY CLAIMS
  A includes: ... / excludes: ...
  B includes: ... / excludes: ...

DIFFERENCE CLASSIFICATION
  Vocabulary-layer differences: [count and examples]
  Concept-boundary-layer differences: [count and examples]
  Ambiguous: [count]

VARIANCE ATTRIBUTION
  Dominant layer: vocabulary / concept-boundary
  Explaining prior: [temporal / domain / institutional / purpose]
  Minimal test case: [object or scenario that falls inside A but outside B, or vice versa]
```

## Integration with Cross-Model Research Synthesis

Use cross-model-research-synthesis first to collect and surface disagreements. When synthesis produces an irreconcilable conflict — where no single coherent answer can incorporate both sources — hand off to this skill. The output of prior differential analysis tells you whether the synthesis failed because of terminology (fixable by normalization) or because the sources are answering genuinely different questions under the same label (not fixable by synthesis, requires explicit disambiguation in the final output).
