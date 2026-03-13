# Spin Glass Models and Gaussian Bounds

**Tags:** probability, statistical-physics, spin-glass, gaussian-comparison, sidak-inequality

## Core Problem

Bounding the maximum of correlated Gaussian fields arising from spin glass Hamiltonians.

## Key Model

p-spin Hamiltonian:
$$H_N(\sigma) = \sqrt{N/2} \sum_{i_1,...,i_p} g_{i_1,...,i_p} \sigma_{i_1}\cdots\sigma_{i_p} / N^{p/2}$$
where g are i.i.d. standard Gaussians, σ ∈ {-1,1}^N.

## Main Tool: Khatri-Sidak Inequality

For centered Gaussian (X_1,...,X_n) with arbitrary covariance:
$$P(\max_j |X_j| \leq x) \geq \prod_j P(|X_j| \leq x)$$

### Application
- Decoupling: bound max over 2^N configurations by product of marginals
- Each marginal is standard Gaussian → P(|X_j| ≤ x) = erf(x/√2)
- Optimizing over x yields: lim sup E[N^{-1} max_σ |H_N(σ)|] ≤ √(ln 2)

## Results Proven

1. **Expected maximum bound**: ≤ √(ln 2) for arbitrary p-spin model
2. **Median bound**: Same √(ln 2) bound via concentration
3. **Comparison with Parisi formula**: For SK model (p=2), Parisi value ≈ 0.7633 < √(ln 2) ≈ 0.8326

## Open Extensions

- Spherical spin glass model (continuous spins on S^{N-1})
- Quaternion Gaussian extension of Sidak inequality
- Mixed p-spin models
- Tighter bounds via Sudakov-Fernique or Gordon's inequality
- Connection to free energy computations

## References

- Wei, draft on Sidak inequality applications (2014-2015)
- Talagrand, "Mean Field Models for Spin Glasses" (Springer)
