---
activation_trigger: "Apply when upper-bounding the expected maximum of exponentially many correlated Gaussians using the Khatri-Sidak inequality in five algebraic steps."
---

# Spin Glass Maximum Bounds via Khatri-Sidak

**Tags:** probability, statistical-physics, spin-glass, gaussian-comparison, sidak-inequality

**Purpose:** Upper-bound the expected maximum of exponentially many correlated Gaussians in five algebraic steps, without solving a variational problem.

## Trigger Conditions

You need this skill when you recognize ANY of these upstream signatures:

1. **You're optimizing over binary vectors and the objective is a sum of random coefficients.** Specifically: you have max_{σ ∈ {-1,1}^N} f(σ) where f is a polynomial in σ with Gaussian coefficients. This IS a spin glass Hamiltonian — you're bounding its ground-state energy.
2. **You have 2^N (or more) correlated Gaussian random variables and need a ceiling on E[max].** The correlation structure is complicated or unknown, and you don't want to characterize it. Khatri-Sidak decouples the maximum with zero assumptions on covariance.
3. **Someone claims a ground-state energy value and you need a one-line falsification.** If their value exceeds √(ln 2) · N per spin, it violates the universal bound — reject without further analysis.
4. **You're benchmarking a heuristic optimizer on a random instance.** The bound √(ln 2) · N gives you a method-independent ceiling to measure optimality gaps against.

## Decision Procedure: Is This the Right Tool?

Check in order. Take the FIRST match:

| # | Condition | Tool | Why not Khatri-Sidak |
|---|-----------|------|---------------------|
| 1 | You need a **lower** bound on E[max] | Sudakov minoration | Khatri-Sidak only gives upper bounds |
| 2 | You have two Gaussian fields with pointwise-ordered covariances (q₁(s,t) ≤ q₂(s,t) everywhere) | Sudakov-Fernique comparison | Gives tighter relative bounds between the two fields |
| 3 | The index set is a **sphere or convex body**, not {-1,1}^N | Gordon's min-max inequality | Exploits continuous geometry Khatri-Sidak ignores |
| 4 | You need **concentration** around the mean, not a bound on the mean itself | Borell-TIS inequality | Different question entirely |
| 5 | You need the **exact** asymptotic value | Parisi formula | Khatri-Sidak is ~9% loose for p=2 |
| 6 | None of the above match | **→ Use Khatri-Sidak** | Universal, assumption-free, five steps |

## Key Inequality (Khatri-Sidak)

For centered Gaussian (X₁,...,Xₙ) with **arbitrary** covariance:

$$P(\max_j |X_j| \leq x) \geq \prod_j P(|X_j| \leq x)$$

No assumptions on covariance. This decouples the joint event into independent marginals.

## Procedure: Bound E[max |H_N(σ)|] / N

Concrete setup — p-spin Hamiltonian:
$$H_N(\sigma) = \frac{\sqrt{N}}{\sqrt{2}\, N^{p/2}} \sum_{i_1,...,i_p} g_{i_1,...,i_p}\, \sigma_{i_1}\cdots\sigma_{i_p}$$
with i.i.d. standard Gaussians g, configurations σ ∈ {-1,1}^N.

**Step 1 — Verify marginals.** Confirm each H_N(σ) is centered Gaussian with Var[H_N(σ)] = 1. If not, rescale. Everything downstream assumes unit variance.

**Step 2 — Apply Khatri-Sidak.** Decouple the 2^N correlated terms:
$$P\bigl(\max_{\sigma} |H_N(\sigma)| \leq x\bigr) \geq \prod_{\sigma \in \{-1,1\}^N} P(|H_N(\sigma)| \leq x)$$

**Step 3 — Substitute marginals.** Each factor is a standard Gaussian tail:
$$\prod_{\sigma} P(|H_N(\sigma)| \leq x) = \bigl[\text{erf}(x/\sqrt{2})\bigr]^{2^N}$$

**Step 4 — Extract the bound.** Set the product equal to 1/2 and solve for the threshold x_N:
$$2^N \ln\,\text{erf}(x_N/\sqrt{2}) = \ln(1/2)$$
For large x, use the Gaussian tail approximation: 1 - erf(x/√2) ≈ e^{-x²/2}/(x√π), giving ln erf(x/√2) ≈ -e^{-x²/2}/(x√π). Substitute:
$$2^N \cdot \frac{e^{-x_N^2/2}}{x_N\sqrt{\pi}} \approx \ln 2$$
Solve for x_N: Take logarithms of both sides:
$$N\ln 2 - \frac{x_N^2}{2} - \ln(x_N\sqrt{\pi}) \approx \ln(\ln 2)$$
For large N, the dominant balance is N ln 2 ≈ x_N²/2, giving:
$$x_N \sim \sqrt{2N\ln 2}$$
Thus:
$$\limsup_{N\to\infty} \frac{1}{N}\, E\!\left[\max_{\sigma} |H_N(\sigma)|\right] \leq \sqrt{\ln 2}$$

**Step 5 — Sanity check.** The bound must sit ABOVE known exact values. For SK model (p=2): Parisi value ≈ 0.7633 < √(ln 2) ≈ 0.8326. ✓ If your bound falls below a known value, you have a normalization error — return to Step 1.

## Calibration

- √(ln 2) ≈ 0.8326 is **universal** across all p-spin models (p ≥ 2)
- Gap to truth: ~9% for p=2 (SK), shrinks as p → ∞ (approaches REM)
- Sufficient for order-of-magnitude estimates and falsification; insufficient for phase-transition analysis

## References

- Wei, draft on Sidak inequality applications (2014-2015)
- Talagrand, *Mean Field Models for Spin Glasses* (Springer)
