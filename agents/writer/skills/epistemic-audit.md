# Epistemic Audit

## One-liner
Append an `Epistemic Status` section to each substantive article so readers can judge how well the draft's claims are grounded.

## When to Use
- Use for substantive articles, essays, analysis posts, technical explainers, reviews, and opinion pieces with factual, causal, predictive, or evaluative claims.
- Do not use as a substitute for verification. If a central claim cannot be grounded, source it, hedge it, remove it, or flag it for human review.

## Methodology

1. **Extract major claims**
   - Identify the thesis.
   - Identify factual claims, causal claims, predictions, interpretations, and strong evaluative judgments.
   - Ignore purely stylistic transitions unless they smuggle in a claim.

2. **Check source grounding**
   - Mark each major claim as `Verified`, `Inferred`, `Assumed`, or `Unsupported`.
   - `Verified`: directly grounded in a cited source, provided source material, or concrete primary evidence.
   - `Inferred`: reasoned from grounded evidence, but not directly stated by a source.
   - `Assumed`: needed for the argument but not proven in the draft.
   - `Unsupported`: asserted without adequate evidence or reasoning.

3. **Rate confidence**
   - `High`: source-grounded and unlikely to change the article's conclusion if challenged.
   - `Medium`: plausible and reasoned, but dependent on interpretation, incomplete evidence, or contested framing.
   - `Low`: speculative, weakly sourced, dependent on memory, or outside the writer's domain expertise.

4. **List alternatives considered**
   - Name the strongest competing explanation, interpretation, or counterargument.
   - State why the draft favors its current framing.
   - If the alternative is nearly as plausible, lower the confidence rating.

5. **Highlight assumptions and gaps**
   - List assumptions the reader must accept for the argument to work.
   - Flag unsupported claims that remain in the final draft.
   - Note where domain expertise, missing data, or unavailable sources could change the conclusion.

6. **Revise before appending**
   - Remove or hedge unsupported claims that are not necessary.
   - Add source references for central claims when available.
   - Keep the audit compact; it should clarify trustworthiness, not bury the article.

## Output Format

Append this collapsible section before the references:

```html
<details>
<summary>Epistemic Status</summary>

- **Overall confidence:** High / Medium / Low.
- **Source grounding:** What is verified, inferred, assumed, or unsupported.
- **Key assumptions:** The assumptions the article depends on.
- **Alternatives considered:** The strongest competing explanations or interpretations.
- **Unsupported or weak claims:** Claims that need more evidence, hedging, or human review.

</details>
```
