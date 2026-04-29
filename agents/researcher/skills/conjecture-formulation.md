---
activation_trigger: "Apply when moving from observed patterns to a precisely stated, falsifiable conjecture that should be stress-tested before any proof attempt."
---

# Conjecture Formulation and Testing

**Tags:** math, research, conjectures, counterexamples, discovery, problem-solving

## Core Principle
A conjecture is a bet: make it precisely enough to be falsifiable, test it aggressively before investing proof effort, and treat disconfirmation as data rather than failure — a good counterexample is more valuable than a decade of wasted proof attempts.

## Technique

Mathematical progress often runs: observe → conjecture → test → refine → prove (or disprove). Most of the intellectual work happens before the proof. Skilled conjecturing is a learnable discipline.

---

### Phase 1: Observation and Pattern Extraction

The raw material for conjectures is examples and data.

**How to generate high-quality examples:**
- **Compute small cases systematically.** For combinatorial or number-theoretic problems, list the first 20-50 cases and look for patterns. Do not cherry-pick.
- **Look for invariants.** What quantity is preserved across examples? What quantity always changes by exactly 1? Invariants are often theorem statements.
- **Look for monotonicity.** Does the quantity always increase? Always decrease? Monotone sequences suggest inductive arguments.
- **Compare extreme cases.** What happens at n=0, n=1, n→∞? What happens when parameters are equal? What happens when one parameter dominates?
- **Vary one parameter while fixing others.** Isolate dependencies.

**Pattern types to notice:**
- Exact equality (→ likely provable by construction or counting)
- Divisibility or modular patterns (→ number-theoretic or algebraic argument)
- Inequality or bound (→ possibly extremal, possibly tight)
- Asymptotic ratio tending to a constant (→ asymptotic analysis)
- Threshold behavior (→ phase transition, often hard)

---

### Phase 2: Formulating the Conjecture

**Precision requirements:**
- State the conjecture as a formal mathematical sentence with all quantifiers explicit: "For all n ≥ 2 and all graphs G on n vertices with minimum degree ≥ n/2, ..."
- Specify the domain precisely: integers? positive integers? real numbers? compact sets? smooth functions?
- If you observed the pattern for n ≤ 20, say so. The conjecture extrapolates — be honest about evidence range.

**Strength calibration — conjecture at the right level:**
- Start with the weakest form you believe: "f(n) = O(n²)."
- If this seems provable, strengthen: "f(n) ≤ Cn² for an explicit C."
- Strengthen further: "f(n) ~ Cn² as n → ∞."
- Maximum strength: "f(n) = Cn² + O(n log n) with C = π/6."
- The strongest form that fits the data is the most interesting — but also the most likely to fail.

**Naming the conjecture's claim type:**
- **Universal claim:** "For all X, P(X) holds." — one counterexample refutes it.
- **Existence claim:** "There exists X such that P(X)." — one construction proves it.
- **Asymptotic claim:** "f(n)/g(n) → L." — requires estimation, not just examples.
- **Structural claim:** "Every such object has property P." — usually hardest, most valuable.

---

### Phase 3: Testing Before Proving

**This step is mandatory.** Do not begin a proof before testing.

**Testing protocol:**
1. **Verify all known examples satisfy the conjecture.** If any fails, the conjecture is dead — analyze the failure (see Phase 4).
2. **Generate adversarial examples.** Try to find a counterexample by constructing the "worst case" — the object most likely to violate the property.
3. **Test boundary cases.** n=0, n=1, degenerate objects, extremal configurations, objects where the hypothesis is barely satisfied.
4. **Test randomly.** For conjectures about continuous or high-dimensional objects, sample randomly. A true conjecture should survive 1000 random trials.
5. **Scale up.** Verify for n ≤ 100, then n ≤ 10,000. If computationally feasible, push further. Asymptotic conjectures may only become visible at large n.

**How hard to test:**
- Combinatorial conjectures (graphs, sequences): exhaustive for n ≤ 15-20, random sampling for larger.
- Number-theoretic conjectures: check first 10⁶ integers with a simple sieve or script.
- Analytic conjectures: check numerically to 10-15 decimal places. A discrepancy of 10⁻¹² is suspicious.

---

### Phase 4: Counterexample Analysis and Conjecture Refinement

A counterexample is not the end — it is diagnostic information.

**When a counterexample is found:**
1. **Study the counterexample.** What makes it special? Which hypothesis is it violating? Which structural feature makes it fail?
2. **Identify the failure mode.** Is this an exception to a general rule, or does it reveal the conjecture is fundamentally wrong?
3. **Refine the conjecture.** Add a hypothesis that excludes the counterexample while preserving generality. Be careful not to just patch by exclusion — ask whether the amended conjecture still has the same spirit.
4. **Test the refined conjecture.** Repeat from Phase 3.
5. **Sometimes: flip the conjecture.** If many counterexamples arise, the correct statement may be the reverse: "most objects fail P" rather than "all objects satisfy P."

**Calibration of strength after testing:**
- If all tests pass for n ≤ 1000 but you have no proof: "Conjecture (computationally verified for n ≤ 1000)."
- If standard techniques clearly suffice: "Lemma (routine)."
- If a proof exists but is hard: "Theorem." The conjecture label should dissolve once proved.

---

### Phase 5: Deciding Whether to Prove or Abandon

Not every tested conjecture deserves a proof attempt.

**Green light for investing proof effort:**
- Conjecture passes all aggressive tests.
- The statement would be useful if true (either for further results or intrinsically interesting).
- You can sketch a proof path — you have a rough strategy in mind.
- The conjecture connects to known techniques (analogy to proved results).

**Red flags — reconsider:**
- The conjecture is only marginally better than known results (not worth the difficulty).
- The only known analogy is a hard open problem (your conjecture may be equally hard).
- No proof sketch at all after serious thought (may be false in a subtle way not yet found).
- The conjecture holds for many examples but fails for a very specific family you have not checked.

---

### Heuristics for Generating Conjectures

From working mathematicians:

- **"What would make this true?"** — Work backwards: what hypotheses would allow a clean proof? Then check whether those hypotheses hold in your examples.
- **Upgrade equalities to identities.** If two quantities happen to be equal in all examples, conjecture they are equal in general — then look for a bijection, an algebraic identity, or a generating function explanation.
- **Look for the "right" formulation.** Often a conjecture is hard because it is stated in coordinates. Try reformulating invariantly, projectively, or categorically.
- **Dimensional analysis.** In combinatorics and analysis, a conjecture of the form f(n) = nᵅ · lower-order terms must have α match what dimensional considerations predict. Wrong exponents are immediately wrong.
- **Use OEIS.** Compute the first 15 terms of a combinatorial sequence and search the OEIS (oeis.org). If it is known, read the references. If it is new, you may have found something worth publishing.

## Application

When investigating a new mathematical phenomenon:

1. **Compute at least 20 concrete examples.** Record them in a table.
2. **State the pattern as a precise conjecture** with all quantifiers. Write it down.
3. **Generate 5 adversarial examples** specifically designed to stress the conjecture.
4. **Write a short script** to verify the conjecture computationally for n ≤ 1000 (or equivalent).
5. **If no counterexample found**, note "verified computationally for [range]" and begin looking for a proof sketch.
6. **If counterexample found**, analyze it, refine the conjecture, return to step 3.
7. **State the final conjecture** with its evidence base, explicitly flagging what is proved vs. computed.
