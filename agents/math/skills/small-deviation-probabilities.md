# Small Deviation Probabilities

**Tags:** probability, gaussian, small-deviation, negative-moments, determinants

## Core Idea

Estimate P(||X|| < ε) as ε → 0 for Gaussian processes X, via connection to negative moments and integral representations.

## Key Connections

### Negative Moments ↔ Small Deviations
- E|X|^{-α} = α ∫_0^∞ t^{α-1} P(|X| < 1/t) dt
- Tail behavior of P(|X| < ε) determines finiteness of negative moments
- Conversely, negative moment bounds yield small deviation estimates

### Integral Representation
- |x|^{-α} = C_α ∫_0^∞ t^{α-1} cos(tx) dt for α ∈ (0,1)
- Applied to Gaussian quadratic forms: E|⟨X,AX⟩|^{-α} via Fourier analysis
- Determinant formula: involves det(I + 2itΣA)^{-1/2}

## Main Results Available

1. **Gaussian Matrix Determinant**: P(|det E| < ε) ≤ ε^{1-δ} for symmetric Gaussian matrices
2. **Gaussian Hadamard Conjecture**:
   lim sup n^{-1}(log n)^{-1} log P(max_{j<k}|Σ_i ξ_{ij}ξ_{ik}| < 1) ≤ -1/4
3. **Self-Repelling Brownian Motion**:
   lim sup ε^{2/γ} log P(H_γ < ε) ≤ -1/(2e)
   where H_γ = ∫∫ |B_s - B_t|^γ ds dt

## Proof Techniques

- **Blocking method**: Partition variables into independent blocks, apply product bounds
- **Permanent/determinant bounds**: per(Σ) controls product expectations
- **Chebyshev optimization**: Optimize P(X < ε) ≤ E[e^{-λX}] · e^{λε} over λ
- **Hölder's inequality**: Interpolation between different moment orders
- **Residue theorem**: Evaluate complex integrals from Fourier representations

## Publication Potential

The draft `small_deviation_negative_moment_20140816.tex` contains:
- Sections 1-4: rigorous, self-contained (small deviation paper)
- Hadamard conjecture section (independent paper)
- Self-repelling BM section (independent paper)
Could yield 2-3 journal articles.

## References

- Wei, draft on small deviations and negative moments (2014)
- Li & Shao, "Gaussian processes: inequalities, small ball probabilities..." (survey)
