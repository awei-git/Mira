---
activation_trigger: "Apply when a mathematical statement needs a proof and you must select the most effective technique by reading its structural signature before attempting a solution."
---

# Proof Strategy Selection

**Tags:** math, proof, reasoning, problem-solving, strategy

## Core Principle
Match the proof technique to the structural signature of the problem before attempting a solution.

## Technique

Every mathematical statement has structural cues that point toward the most effective proof method. Reading these cues before diving in saves enormous effort and prevents dead ends.

**Strategy catalog and when to use each:**

1. **Direct proof** — Use when the statement has the form "if P then Q" and there is a clear chain of implications from hypothesis to conclusion. Look for: definitions you can unpack, algebraic identities you can manipulate, or known inequalities you can chain.

2. **Proof by contradiction** — Use when the conclusion is hard to approach directly but its negation produces something concrete to work with. Strong signals: uniqueness claims ("there is exactly one"), non-existence claims ("no such X exists"), irrationality proofs, and statements where the negation creates a finite or countable object you can analyze.

3. **Mathematical induction** — Use when the statement is parameterized by natural numbers or has recursive structure. Variants matter:
   - *Simple induction* for statements about n where the (n+1) case directly uses the n case.
   - *Strong induction* when the (n+1) case may need cases below n.
   - *Structural induction* for trees, formulas, or recursively defined objects.
   - *Transfinite induction* for well-ordered sets beyond the naturals.

4. **Construction/exhibition** — Use for existence proofs ("there exists an X such that..."). Build the object explicitly. When direct construction is hard, consider probabilistic method (show a random object has the property with positive probability) or algebraic construction (use polynomials, field extensions, etc.).

5. **Pigeonhole principle** — Use when the problem involves placing more objects than containers, or when cardinality arguments force a collision. Signal phrases: "at least two," "some pair must," "among any n+1 elements."

6. **Counting in two ways (double counting)** — Use when the problem involves a quantity that can be computed from two different perspectives. Equating the two counts often yields the desired identity or inequality.

7. **Extremal principle** — Use when you can consider a minimal or maximal counterexample and derive a contradiction, or when selecting an extreme element simplifies the configuration.

**Decision procedure:**
- Parse the logical form of the statement (for-all, exists, for-all-exists, etc.).
- Identify the mathematical domain (combinatorics, algebra, analysis, topology).
- Check for recursive/inductive structure.
- Ask: "Is the negation more tractable than the statement itself?" If yes, try contradiction.
- Ask: "Can I build the object?" If existence claim, try construction first.
- Ask: "Is there a size mismatch?" If yes, try pigeonhole.
- If multiple strategies seem viable, start with the one that requires the fewest auxiliary lemmas.

## Application

When given a mathematical problem:

1. **Before writing any equations**, spend time classifying the statement. Write down its logical skeleton: "For all X in S, if P(X) then Q(X)" or "There exists X in S such that P(X)."
2. Consult the strategy catalog above and identify 1-2 candidate approaches.
3. For each candidate, sketch the proof outline in 2-3 sentences. Which one has fewer gaps?
4. Commit to the approach with the clearest path and execute.
5. If stuck after meaningful effort, return to step 2 and try the next candidate rather than forcing the current approach.
