# Mathematical Problem-Solving Heuristics

**Tags:** math, problem-solving, heuristics, Polya, strategy, research

## Core Principle
Before calculating, invest time in understanding — understanding the problem's structure, its analogies to solved problems, and the shape of a potential solution. Most mathematical stuck-points are failures of understanding, not failures of technique.

## Technique

George Pólya's *How to Solve It* (1945) and its successors identify recurring mental moves that expert mathematicians make. These are not algorithms but **heuristics** — strategies that usually help and are worth trying when you do not know what to do next.

---

### Pólya's Four-Phase Framework

**Phase 1: Understand the Problem**

Before doing anything else:
- State the problem in your own words. Can you state it more simply?
- What are the unknowns, data, and constraints? Write them down explicitly.
- Draw a figure if the problem is geometric. Label everything.
- What does a solution look like? (What form would the answer take?)
- Is the problem well-posed? Are there enough conditions to determine the answer, or too many?
- Introduce notation. Give names to the key quantities.

**Phase 2: Devise a Plan**

This is where heuristics apply (see detailed list below). The core question: **Have you seen this or something like it before?**

**Phase 3: Carry Out the Plan**

- Execute the strategy chosen. Check every step as you go.
- If you cannot see clearly that a step is correct, prove it before proceeding.
- Maintain notation discipline: write clearly, be explicit about what each symbol denotes.

**Phase 4: Look Back**

- Verify the result (can you check it another way?).
- Can you derive the same result by a different method? This usually yields deeper understanding.
- Can you use the result or the method to solve a related problem?
- What was the key idea? Record it — it will recur.

---

### The Core Heuristic Moves

**1. Work backwards (retrograde analysis)**
Start from the desired conclusion and ask: what would imply this? What would give me this? Keep backchaining until you reach something you know or can prove directly.
- *When to use:* goal is clear but path to it is not; constructive proofs; optimization.
- *Tao's formulation:* "What would I need to know in order to conclude this?"

**2. Reduce to a simpler problem**
Solve a special case first: n=2 instead of general n; continuous instead of discrete; 1D instead of nD; bounded domain instead of full space. A clean solution to the special case often generalizes or reveals the key obstacle.
- *Variants:* Let all variables be equal (symmetry reduction). Let one variable go to 0 or ∞ (limiting case). Fix all but one variable.

**3. Find an analogy**
Has a similar problem appeared in another area of mathematics? The same logical structure in a different domain may already be solved. Examples:
- Combinatorial identity that mirrors an integral formula
- Graph-theoretic problem with a linear algebraic analogue
- Discrete problem with a continuous analogue solved by calculus
- Finite-group result with an analogue for Lie groups

**4. Introduce an auxiliary element**
Add a construction that is not in the problem but makes it tractable: draw an auxiliary line, introduce a new variable, pass to a covering space, tensor with a field extension, add and subtract the same term. The auxiliary element often reveals hidden symmetry.

**5. Exploit symmetry**
Ask: what symmetries does the problem have? If a quantity is symmetric under a group action, its average equals its value (for invariant averaging). If the conclusion is symmetric, the proof should reflect that. Symmetry can dramatically reduce cases or suggest the correct algebraic framework.

**6. Enumerate cases exhaustively**
When the problem has finitely many configurations, list them all. This is never elegant but it is reliable. Use it to:
- Verify that the problem has no surprising exceptions.
- Find patterns in the case analysis that suggest a uniform argument.
- Sometimes the case analysis IS the proof.

**7. Find invariants**
What quantity is preserved as you apply the allowed operations? If you are trying to reach a configuration from another, and an invariant differs between them, it is impossible. Invariants:
- Kill impossibility problems immediately.
- Suggest the right algebraic structure to use.
- For combinatorial games: monovariant (a quantity that strictly decreases each step) proves termination.

**8. Use extreme cases / extremal principle**
Consider the object that maximizes or minimizes a relevant quantity among all objects satisfying the hypotheses. The extremal object often has additional structure (forced by the extremality) that makes the proof proceed.
- *Typical argument:* Suppose G is a minimal counterexample. Then [extremality implies structural property]. But then G cannot be a counterexample.

**9. Think probabilistically**
Assign probabilities uniformly or strategically. If the expected value of a quantity is > 0, then some specific instance achieves it. The probabilistic method (Erdős) is among the most powerful existence techniques in combinatorics.

**10. Reformulate / change representation**
- Change coordinates (polar, projective, logarithmic).
- Replace a function by its Fourier transform, generating function, or Laplace transform.
- Encode a combinatorial structure as a polynomial (generating function), a matrix (adjacency/transfer), or a graph.
- Translate between algebra and geometry (Nullstellensatz, algebraic geometry).

**11. Use small-scale computation to discover the proof**
When stuck, compute several cases by hand and watch what you are doing. The pattern of computation often contains the proof. Ask: "Why did this step work?" — the answer is usually the key lemma.

**12. Ask "what if I already had a solution?" (Proclus' method)**
Assume you have a solution. What properties must it have? What equations must it satisfy? Often the answer to "what does a solution look like?" determines the solution uniquely or greatly constrains it.

---

### Higher-Level Research Heuristics (beyond competition problems)

**Know what you want to prove before you know how to prove it.**
State the desired theorem as cleanly and precisely as possible before searching for a proof. Vague goals produce vague proofs.

**Spend time at the boundary.**
The most interesting results live at the boundary of what is known. Look for the exact hypotheses at which theorems fail — the boundary cases teach you more than the interior.

**Attack from multiple angles simultaneously.**
While trying direct proof, also try contradiction. While trying induction, also try generating functions. Running parallel attempts avoids tunnel vision and often yields a hybrid proof.

**Lower the difficulty, raise the difficulty, then find the right difficulty.**
If the problem is hard, try an easier version. If the easy version has a nice proof, try to push it up to the original. If the easy version is also hard, try an even easier version. This binary search on difficulty is a standard research move.

**Look for the "why".**
A proof that consists of calculations may be correct but not explanatory. Ask: what is the real reason this is true? A conceptual explanation is usually shorter, generalizes better, and is remembered longer.

**Tao's principle: "Ask yourself dumb questions."**
The obvious questions ("why does the theorem require this hypothesis?", "what is the simplest possible example?", "what happens if I remove this condition?") are the most important and most often skipped.

---

### When Stuck: Emergency Protocol

1. **State precisely what you know and what you want.** Write both down in detail. Often the gap becomes clear.
2. **Find the smallest case where you are stuck.** If the proof breaks for n=3, understand n=3 completely.
3. **Look for analogies in other areas.** What other problem has the same logical shape?
4. **Sleep on it.** Non-metaphorical advice: research has shown that insight often comes after stepping away. Set the problem aside and return later.
5. **Change the representation.** Draw a picture if you have not. Write it as a matrix. Write it as a graph. Write it as a generating function.
6. **Consult the literature.** Someone may have solved a lemma you need. This is not cheating — it is how mathematics works.
7. **Accept that you may need a new idea.** If standard techniques do not apply, the problem may require genuinely new mathematics. This is rare but real.

## Application

When given a mathematical problem:

1. **Spend the first 10-20% of your effort understanding before calculating.** Write the logical skeleton of the statement, name all objects, draw a figure.
2. **Identify which heuristics apply** from the list above. Rank them by plausibility given the problem domain.
3. **Try one approach for a fixed effort budget** (say 20 minutes or 3 pages of scratch work). If no progress, switch.
4. **After a solution is found**, apply the "look back" phase. State the key insight in one sentence. Generalize if possible.
5. **If completely stuck**, try the emergency protocol above before abandoning the problem.
