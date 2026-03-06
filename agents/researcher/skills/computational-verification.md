# Computational Verification

**Tags:** math, computation, verification, conjectures, experimental-math

## Core Principle
Use computation to test conjectures, find counterexamples, and build intuition before committing to formal proof — let the computer handle enumeration so the human (or agent) handles insight.

## Technique

Computation is not opposed to proof; it is the scouting party that goes ahead of proof. The right computation at the right time can save days of wasted effort on false conjectures or reveal the pattern that makes a proof obvious.

**Three modes of computational verification:**

### 1. Conjecture testing
Before trying to prove a statement, verify it computationally for small cases.

- **Exhaustive check for small n:** If you conjecture something holds for all n, check n = 1 through n = 20 (or n = 1000 if feasible). A single failure kills the conjecture early.
- **Random sampling:** For statements about real-valued functions or high-dimensional objects, sample random inputs and check. This catches "generic" failures fast.
- **Boundary and edge cases:** Test n = 0, n = 1, degenerate configurations, extremal values. Many false conjectures fail at boundaries.

### 2. Counterexample search
When you suspect a statement might be false, computation is the fastest way to find a counterexample.

- **Systematic enumeration:** Generate all objects of a given size and filter for those violating the claim.
- **Constraint satisfaction:** Encode the negation of the statement as a constraint problem (SAT, SMT, ILP) and let a solver find a witness.
- **Randomized search with heuristics:** For large search spaces, use random generation biased toward likely counterexample regions.

### 3. Pattern discovery and intuition building
Computation can reveal structure that is invisible from a few examples.

- **Sequence generation:** Compute the first 50 terms of a sequence and look it up in OEIS. Known sequences carry known theory.
- **Visualization:** Plot functions, graph adjacency matrices, draw polytopes. Spatial intuition often suggests the right approach.
- **Symbolic computation:** Use CAS (computer algebra systems) to factor, simplify, or find closed forms. Let the machine handle algebraic complexity.
- **Numerical stability check:** For conjectures involving limits, convergence, or asymptotics, compute numerically to high precision and check whether the pattern is genuine or an artifact of low precision.

**Computational hygiene:**

- **Distinguish verification from proof.** Checking 10^6 cases is strong evidence, not proof. Always state explicitly: "verified for n <= N, proof pending."
- **Watch for numerical artifacts.** Floating-point arithmetic lies. If a result depends on exact equality of floating-point numbers, use rational or symbolic arithmetic instead.
- **Automate reproducibly.** Write scripts that can be re-run, not one-off REPL commands. When a conjecture evolves, you need to re-verify quickly.
- **Scale up systematically.** Start with the smallest non-trivial case. If it passes, increase size. Track computation time — if it grows too fast, you need a smarter algorithm or a reformulation.

## Application

When investigating a mathematical conjecture or exploring a new problem:

1. **Formulate the statement precisely enough to compute.** If you cannot write code to check it, you do not understand it well enough.
2. **Write a verification script** for small cases. Use Python with sympy/numpy/sage, or a CAS like Mathematica. Run it.
3. **If all cases pass:** Look at the data. Does it suggest a stronger conjecture? A closed-form formula? A recursive structure? Use the computational output to guide proof strategy.
4. **If a case fails:** You found a counterexample. Analyze it. What makes it special? Can you weaken the conjecture to exclude it, or does it fundamentally refute the direction?
5. **Document the computational evidence** alongside any formal proof. Future work benefits from knowing what was checked and to what extent.
