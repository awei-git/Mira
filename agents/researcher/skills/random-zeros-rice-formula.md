---
activation_trigger: "Apply when computing the expected number of zeros of a random Gaussian polynomial, harmonic function, or analytic function using the Rice/Kac-Rice formula."
---

# Random Zeros and Rice Formula

**Tags:** probability, random-polynomials, complex-analysis, rice-formula, harmonic-polynomials

## Core Technique

Counting expected zeros of random polynomial/harmonic/analytic functions using Rice formula for Gaussian random fields.

## Rice Formula Framework

For a smooth Gaussian random field Y: R^d → R^d, the expected number of zeros in domain D:
$$E N(D) = \int_D E[|det ∇Y(x)| \mid Y(x)=0] · p_{Y(x)}(0) dx$$

### Application to Harmonic Polynomials

h_{n,m}(z) = p_n(z) + \overline{q_m(z)} with Gaussian coefficients:
- Separate into real/imaginary parts: Y = (Re h, Im h)
- Compute conditional density and Jacobian determinant
- Reduce to computing E|det J| for Gaussian quadratic forms

### Key Results

1. **Expected count**: E N_{n,n} ~ (π/4) n^{3/2} as n→∞ (Li-Wei, PAMS 2009)
2. **Asymmetric case**: E N_{n,αn} ~ n for α < 1
3. **Gravitational lensing**: Lens equation as harmonic function → image count via Rice formula

## Variance and Higher Moments

Second moment requires:
- det∇Y decomposition into Gaussian quadratic forms
- Two-point Rice formula: E[N(D)(N(D)-1)] = ∫∫ (joint density calculation)
- Leads to integrals involving 4×4 covariance matrices
- Residue theorem for evaluation; conditions on sign of 2c+2a₁a₂-b²

**Status**: Second moment (variance) partially worked out for n=2; higher moments and CLT open.

## Proof Ingredients

1. Kac-Rice formula setup (separate real/imaginary parts)
2. Gaussian conditional distribution computation
3. Absolute value of quadratic form expectations (→ gaussian-moment-inequalities skill)
4. Asymptotic analysis: saddle point method, Laplace approximation
5. Contour integration and residue theorem for explicit evaluation

## Open Directions

- Variance asymptotics for harmonic polynomials (draft exists, incomplete)
- CLT for zero count
- Hole probability for ±1 random polynomials near z=1
- Extension to random fields on manifolds
- Zeros of random sections of line bundles (Shiffman-Zelditch program)

## References

- Li & Wei, "Expected number of zeros of harmonic polynomial" (PAMS 2009)
- Wei, "Gravitational lensing models" (JMP 2017)
