# SPDE Uniqueness and Regularity Techniques

**Tags:** stochastic-pde, uniqueness, existence, malliavin-calculus, backward-sde

## Core Problem

Establish existence and strong uniqueness for SPDEs of the form:
$$dv = \frac{1}{2}\Delta v \, dt + f(v, \nabla v) \nabla v \, dF$$
where F is a Gaussian random field with specified covariance structure.

## Key Techniques

### 1. Yamada-Watanabe Method
- For SDEs with non-Lipschitz coefficients (e.g., Hölder continuous σ)
- Construct approximating sequence of smooth functions φ_n ↑ |x|
- Show E[φ_n(X_t - Y_t)] → 0 for two solutions X, Y
- Requires careful control of local time at zero

### 2. Backward Doubly Stochastic DEs (BDSDE)
- Pardoux-Peng framework: both forward and backward stochastic integrals
- Transform SPDE uniqueness into BDSDE uniqueness
- Useful when direct pathwise estimates are difficult

### 3. Malliavin Calculus for Density Smoothness
- Derivative operator D_t on Wiener space
- Criterion: If D_t(u(t,x)) is nondegenerate, density of u(t,x) is smooth
- Applications to semilinear heat equations with multiplicative noise
- Challenge with fractional BM: no independent increments → blocking process fails

### 4. Gaussian Field Covariance Requirements
- Nuclear covariance: Σ(x,y) = min(s,t) σ(|x-y|) with σ integrable
- Spatial correlation function determines regularity
- Dalang's condition for existence in higher dimensions

## Proof Architecture

1. A priori estimates (energy inequalities)
2. Tightness of approximating sequence
3. Identification of limit via martingale problem
4. Uniqueness via comparison or BDSDE approach
5. Regularity via Kolmogorov/Sobolev embedding

## Open Directions

- Fractional noise SPDEs: density smoothness when H ≠ 1/2
- Large deviation principles for SPDEs with scaling parameter
- Connection between noise regularity exponent α and LDP rate β
- Strong uniqueness for wider classes of non-Lipschitz coefficients

## References

- Gomez, Lee, Mueller, Wei, Xiong (SPL 2011)
- Walsh's SPDE notes (in _notes/)
- Malliavin calculus notes (in _notes/)
