# Stochastic Process Problem-Solving

**Tags:** probability, stochastic-processes, brownian-motion, martingales, point-processes

## Activation Gate

Activate this skill IF AND ONLY IF:
1. The user presents a specific stochastic process problem statement, AND
2. The problem requests calculation of: probability, expectation, bound, distribution, or proof of property (martingale, convergence, characterization).

DO NOT activate for: definitions, literature surveys, or open-ended "explain" questions.

## Trigger Table

Match the problem statement against these exact patterns:

| Problem asks for... | Go to |
|---|---|
| Probability or expectation involving Brownian motion hitting time, exit probability, or first passage | §BM step 1 |
| Distribution or property of arithmetic Brownian motion (dX_t = μ dt + σ dB_t) with constant μ, σ | §BM step 2 |
| Solution to PDE using probabilistic representation (Feynman-Kac, Dirichlet) | §BM step 3 |
| Expectation of stopped martingale E[M_τ] | §MG step 1 |
| Probability bound P(sup M_t ≥ λ) for martingale M using Doob/BDG inequality | §MG step 2 |
| Almost sure convergence of martingale sequence | §MG step 3 |
| Distribution of Poisson process arrival times, thinning, or superposition with independent increments | §PPP step 1 |
| Prove a point process is Poisson | §PPP step 3 |
| Finite-dimensional distribution of Gaussian process | §GP step 1 |
| Bound P(sup X_t > u) for Gaussian process X | §GP step 2 |
| Compare E[sup X_i] and E[sup Y_i] for two Gaussian processes | §GP step 3 |
| Representation of exchangeable sequence as mixture of i.i.d. | §EX step 1 |
| Structure of exchangeable random array | §EX step 2 |

### No match? Execute diagnostic:

1. **Transform check:** Does problem explicitly mention Girsanov, Cameron-Martin, Esscher transform, or time-change of a Brownian motion? If yes, apply transform → re-enter trigger table.
2. **Covariance check:** Compute process covariance. If Gaussian → §GP.
3. **Martingale check:** Compute E[X_{n+1} ∣ F_n]. If equals X_n → §MG.
4. **Point process check:** Does problem involve random points in space/time with independent increments? If yes → §PPP.
5. **If still no match:** Report: "Checked for transforms (none), computed covariance (not Gaussian), verified martingale property (absent), examined point process structure (not applicable). Problem does not match known stochastic process classes."

---

## §BM — Brownian Motion / Diffusion

**Step 0: Parse and Classify.** Write the SDE or process definition. Identify: dimension, coefficients (constant/time-dependent), domain, and boundary conditions.
**Step 0a.** Identify filtration. Check if target quantity involves known BM martingale: B_t, B_t² − t, or exp(θB_t − θ²t/2). If yes, use optional stopping.

1. **Hitting time or boundary probability**
   - Apply reflection principle for symmetric boundaries
   - For asymmetric boundaries: use optional stopping with exp(θB_t − θ²t/2), choose θ to match boundary equation
   - Verify: Solution satisfies boundary conditions, probability ≤ 1

2. **Process with drift: X_t = B_t + μt**
   - Apply Girsanov: Define dQ/dP = exp(−μB_T − μ²T/2)
   - Check Novikov: E[exp(½∫₀ᵀ μ² dt)] < ∞
   - Under Q, X_t is standard BM
   - If Novikov fails: measure change invalid

3. **PDE connection**
   - Dirichlet: u(x) = E_x[f(B_τ)] solves Δu = 0 with boundary f
   - Feynman-Kac: u(t,x) = E[exp(−∫V ds) f(B_T)] solves ∂u/∂t + ½Δu − Vu = 0
   - Verify: u satisfies PDE boundary conditions by substitution

4. **Parameter reduction**
   - Use scaling: aB_{t/a²} =ᵈ B_t
   - Reduce to one-parameter family before computation

## §MG — Martingale Problems

**Step 0: Parse and Classify.** Write the process (X_n) or (M_t) explicitly. Identify the filtration (F_n) or (F_t).
**Step 0a.** Compute E[X_{n+1} | F_n] explicitly. For SDE local martingales, check Novikov before treating as true martingale.

1. **Optional stopping for E[M_τ]**
   - Apply if: (a) τ bounded, OR (b) M uniformly integrable, OR (c) τ a.s. finite with |M_{t∧τ}| ≤ C
   - State which condition applies
   - If using (c): exhibit bound C explicitly

2. **Maximal inequality P(sup M_t ≥ λ)**
   - Doob: P(sup_{s≤t} |M_s| ≥ λ) ≤ E[|M_t|^p]/λ^p for p ≥ 1
   - BDG for L²: E[sup|M|^p] ≍ E[⟨M⟩^{p/2}]
   - Verify: Bound must be tighter than Markov on M_t

3. **Convergence**
   - L¹-bounded ⇒ a.s. convergence (forward martingale)
   - Reverse martingale for 0-1 law limits

4. **Local martingale from SDE**
   - Novikov: E[exp(½⟨M⟩_T)] < ∞ ⇒ true martingale
   - If Novikov fails: construct counterexample

## §PPP — Poisson Point Process Problems

**Step 0: Parse and Classify.** Write the space (e.g., ℝ, ℝ², ℝ⁺) and the intensity measure λ(dx). Specify if marked.
**Step 0a.** Write intensity measure λ explicitly (measure on mark space, not just rate).

1. **Thinning**
   - Retain points independently with probability p(x)
   - Result: PPP with intensity p(x)λ(dx)
   - Complementary process independent

2. **Superposition**
   - Sum of independent PPPs = PPP with summed intensities

3. **Prove process is Poisson**
   - Show thinning invariance: independent p-thinning + rescaling by 1/p preserves distribution

4. **Marking or transformation**
   - Attach i.i.d. marks or apply measurable map
   - Result: PPP on product/image space with pushed-forward intensity
   - Verify: Transformed intensity is σ-finite

## §GP — Gaussian Process Problems

**Step 0: Parse and Classify.** Write the mean function μ(t) and covariance kernel K(s,t). Identify the index set T.
**Step 0a.** Identify μ(t) and K(s,t). Verify K is positive semi-definite.

1. **Finite-dimensional marginal**
   - Distribution: N(μ, Σ) where Σ_{ij} = K(t_i, t_j)

2. **Bound P(sup X_t > u)**
   - Borell-Sudakov-Tsirelson: P(sup X_t > E[sup X_t] + u) ≤ exp(−u²/(2σ²_max))
   - σ²_max = sup_t Var(X_t)
   - Verify: E[sup X_t] < ∞ (check via Dudley's entropy if unbounded index)

3. **Compare Gaussian suprema**
   - Sudakov-Fernique: If E[(X_i−X_j)²] ≤ E[(Y_i−Y_j)²] ∀ i,j ⇒ E[sup X_i] ≤ E[sup Y_i]
   - Slepian: Compare P(max X_i > u) when off-diagonal covariances ordered

4. **Centering**
   - Anderson's inequality: For symmetric convex C, centered Gaussian X: P(X ∈ C) ≥ P(X + a ∈ C)

5. **Small ball probability**
   - P(sup|X| < ε) determined by eigenvalue decay in Karhunen-Loève expansion

## §EX — Exchangeability and Invariance

**Step 0: Parse and Classify.** Write the sequence or array (X_i) or (X_{ij}). List the indices that can be permuted.
**Step 0a.** Specify symmetry: permutation of which indices? Under what group?

1. **Exchangeable sequence**
   - De Finetti: X_1, X_2, … conditionally i.i.d. given latent σ-algebra
   - Find mixing measure: lim_{n→∞} (1/n)Σf(X_i)
   - Verify: Sequence must be infinite; finite exchangeability ≠ de Finetti

2. **Exchangeable random array**
   - Aldous-Hoover: X_{ij} = f(α, U_i, U_j, U_{ij})

3. **Thinning-invariant point process**
   - Poisson mixture (point-process de Finetti)