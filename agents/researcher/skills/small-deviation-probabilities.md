---
activation_trigger: "Apply when you need to estimate P(||X|| < ε) as ε→0 for a Gaussian process or determine finiteness of negative moments for a Gaussian functional."
---

# Small Deviation Probabilities

**Tags:** probability, gaussian, small-deviation, negative-moments, determinants

## When to Use

Trigger this skill when you need to:
- Estimate P(||X|| < ε) as ε → 0 for a Gaussian process or Gaussian quadratic form
- Determine whether E|X|^{-α} < ∞ for a Gaussian random variable or process
- Bound the probability that a Gaussian determinant, inner product, or functional is unusually small
- Prove lower bounds on small ball probabilities for Brownian functionals

## Fast Diagnostic

**Check which of these conditions you are working with:**

1.  **You see:** `X = Z^T A Z`, `det(M)` where `M` is Gaussian, or any expectation involving `E[exp(itQ(Z))]`.
    → **Use Technique 1 (Fourier/Determinant)**

2.  **You see:** `P( max_{j<k} |⟨ξ_j, ξ_k⟩| < ε )`, `P( ∏_i X_i < ε )`, or a bound involving a permanent `per(Σ)`.
    → **Use Technique 2 (Blocking)**

3.  **You see:** `P( ∫_0^1 ∫_0^1 f(B_s, B_t) ds dt < ε )` or need to bound `E[exp(-λ H(B))]`.
    → **Use Technique 3 (Chebyshev/Laplace)**

4.  **You see:** The duality integral `E|X|^{-α} = α ∫_0^∞ t^{α-1} P(|X| < 1/t) dt` in your derivation, or need to convert between moment finiteness and tail decay.
    → **Use Core Duality**

## Core Duality: Negative Moments ↔ Small Deviations

The central identity:

> E|X|^{-α} = α ∫_0^∞ t^{α-1} P(|X| < 1/t) dt

**Direction 1 (small deviation → negative moment):**
1. Establish P(|X| < ε) ≤ Cε^β by any method
2. Substitute into the integral
3. Conclude E|X|^{-α} < ∞ for α < β

**Direction 2 (negative moment → small deviation):**
1. Compute or bound E|X|^{-α} directly (moment generating function, explicit density, etc.)
2. Apply Markov's inequality: P(|X| < ε) = P(|X|^{-α} > ε^{-α}) ≤ ε^α · E|X|^{-α}
3. Optimize over admissible α to get the tightest ε-exponent

**Expect:** The critical exponent β (or sup of admissible α) should match the effective dimension of your problem. If it doesn't, check your covariance structure.

## Technique 1: Fourier/Determinant Method

**Use when:** X = ⟨Z, AZ⟩, det(M), or any quadratic Gaussian form.

1. Write |x|^{-α} = C_α ∫_0^∞ t^{α-1} cos(tx) dt (valid for α ∈ (0,1))
2. Exchange the Gaussian expectation and the t-integral (justify by dominated convergence on a truncation)
3. Evaluate the Gaussian integral — you should get det(I + 2itΣA)^{-1/2}
4. Estimate the resulting complex integral: locate poles/branch cuts; apply residue theorem if poles are simple, saddle-point if they are not

**Expect:** For an n×n symmetric Gaussian matrix, the determinant satisfies P(|det E| < ε) ≲ ε^{1-δ} for any δ > 0. If your bound has a worse exponent, check whether off-diagonal correlations are introducing cancellation you haven't accounted for.

## Technique 2: Blocking Method

**Use when:** you need P(max_{j<k} |cross-term| < ε) or bounds on products of dependent Gaussians.

1. Partition variables into independent blocks (choose block size to balance correlation decay vs. block count)
2. Within each block, apply Chebyshev or direct moment bounds on the target quantity
3. Multiply bounds across blocks — independence gives a product of probabilities
4. If the bound involves ∏E|X_i|, control it via permanent bounds: per(Σ) ≤ ∏ σ_i² (Hadamard-type)

**Expect:** For Gaussian cross-correlations, the log-probability should scale as -cn/log(n). Specifically: lim sup n^{-1}(log n)^{-1} log P(max_{j<k}|Σ_i ξ_{ij}ξ_{ik}| < 1) ≤ -1/4. If your exponent is far from this, revisit block size.

## Technique 3: Chebyshev/Laplace Optimization

**Use when:** X is a Brownian path functional, e.g., H = ∫∫ f(B_s, B_t) ds dt.

1. Write P(H < ε) ≤ E[e^{-λH}] · e^{λε} for any λ > 0
2. Compute or bound E[e^{-λH}] — use Feynman-Kac if H involves path integrals, or eigenvalue expansions for quadratic functionals
3. Minimize over λ: differentiate the log-bound and solve for λ*(ε)
4. For interpolation between moment orders (e.g., to sharpen from integer to fractional α), apply Hölder before optimizing

**Expect:** For H_γ = ∫∫ |B_s - B_t|^γ ds dt, the rate is lim sup ε^{2/γ} log P(H_γ < ε) ≤ -1/(2e). The exponent 2/γ in the rate comes from scaling — verify it matches the homogeneity of your functional.

## Quick Reference

| Problem shape | Technique | Key computation |
|---|---|---|
| Gaussian quadratic form / determinant | Fourier representation | det(I + 2itΣA)^{-1/2} |
| Product / max of Gaussian cross-terms | Blocking + permanent bounds | per(Σ), block independence |
| Brownian path functional | Chebyshev / Laplace optimization | Optimize e^{λε} · E[e^{-λH}] over λ |
| Moment finiteness ↔ tail rate | Duality integral | α ∫ t^{α-1} P(|X|<1/t) dt |

**Background:** Li & Shao, "Gaussian processes: inequalities, small ball probabilities..." (survey).
