# Gaussian Moment Inequalities

**Tags:** probability, gaussian, inequalities, permanents, wick-formula

## Core Technique

Bounding expectations of products/absolute values of jointly Gaussian random variables using algebraic structures (permanents, hafnians, determinants).

## Key Tools

1. **Wick Formula**: For centered complex Gaussian Z_1,...,Z_n:
   - E(Z_1···Z_n Z̄_1···Z̄_n) = per(Σ) (permanent of covariance)
   - Extends to quaternion Gaussians with β-determinant (β=1/2,1,2)

2. **Permanent Upper Bound**: E|X_1···X_n| ≤ √(per(Σ))
   - Tight for independent case; improves on Cauchy-Schwarz for correlated variables

3. **Integral Representation of |x|**:
   - |x| = (2/π) ∫_0^∞ (1 - cos(tx))/t² dt
   - |x|^(-α) representations for α ∈ (0,1) via Fourier transform
   - Enables computing E|⟨X,AX⟩ + ⟨b,X⟩|^τ for Gaussian X

4. **Negative Moment Bounds**:
   - E∏|X_j|^{-α_j} ≥ ∏(subgroup products) for α_j ∈ (0,1)
   - Connects to small deviation probabilities

## Proof Patterns

- **Interpolation**: Introduce parameter λ, show monotonicity of g(λ) = E[f(λX + √(1-λ²)Y)]
- **Conditioning**: Condition on subset of variables, apply known inequalities to conditional distribution
- **Complex/Quaternion lifting**: Embed real problem in complex/quaternion setting to access richer algebraic structure
- **Fischer's inequality**: det(Σ) ≤ ∏ det(diagonal blocks) for positive definite Σ

## Open Conjectures from This Line

- Symmetric moment inequality: E∏X_j² ≥ (n choose k)^{-1} Σ_{|A|=k} E∏_{j∈A}X_j² · E∏_{j∉A}X_j²
- Linear polarization: E(X_1^{2p_1}···X_n^{2p_n}) ≥ ∏E X_i^{2p_i} (proof incomplete at eigenvalue step)
- General power extension of permanent bound: E∏|X_j|^{α_j} vs permanent-like expressions

## References

- Li & Wei, "A Gaussian inequality for expected absolute products" (JTP 2010)
- Wei, "Representations of the absolute value function..." (JTP 2014)
- Li & Wei, "Wick formulas for quaternion Gaussian..." (SAAF 2012)
