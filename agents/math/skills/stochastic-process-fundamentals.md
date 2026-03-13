# Stochastic Process Fundamentals

**Tags:** probability, stochastic-processes, brownian-motion, martingales, point-processes

## Core Objects

### Brownian Motion
- Continuous paths, independent Gaussian increments
- Scaling: aB_{t/a²} is standard BM
- Strong Markov property, reflection principle
- Connections: heat kernel, Dirichlet problem, potential theory

### Martingales
- Doob's optional stopping, maximal inequality, convergence theorems
- Reversed martingale convergence (used in thinning invariance proof)
- Local martingales vs true martingales (important for SDE/SPDE)

### Poisson Point Processes
- Thinning: independent retention with probability p → Poisson(pλ)
- Thinning invariance characterization: mixtures of Poisson processes (Starr-Wei JSP 2012)
- Superposition, marking, mapping theorems

### Gaussian Processes
- Covariance structure determines distribution
- Karhunen-Loève expansion: X(t) = Σ √λ_n ξ_n φ_n(t)
- Small ball probabilities: P(sup|X(t)| < ε) as ε → 0
- Comparison inequalities: Sudakov-Fernique, Slepian, Gordon

## Key Inequalities

1. **Borell-Sudakov-Tsirelson**: Concentration of Gaussian supremum around median
2. **Sudakov-Fernique**: E max X_i ≤ E max Y_i if E(X_i-X_j)² ≤ E(Y_i-Y_j)²
3. **Anderson's inequality**: For symmetric convex C, P(X ∈ C) ≥ P(X+a ∈ C)
4. **Slepian's lemma**: Comparison of Gaussian maxima via covariance ordering
5. **Khatri-Sidak**: P(∩{|X_i|≤c_i}) ≥ ∏P(|X_i|≤c_i) for centered Gaussian

## Stochastic Calculus Essentials

- Itô formula, Burkholder-Davis-Gundy inequality
- Girsanov's theorem (measure change)
- Lévy's characterization of Brownian motion
- Stochastic Fubini theorem

## Exchangeability and Invariance

- De Finetti: exchangeable sequences are mixtures of i.i.d.
- Aldous-Hoover: exchangeable arrays → function of uniform r.v.s
- Thinning invariance as probabilistic analogue (Starr-Wei)
- Connections to mean-field models in statistical physics
