# Asymptotic Analysis

**Tags:** math, analysis, asymptotics, big-O, Laplace-method, saddle-point, Stirling

## Core Principle
Extract the dominant behavior of a quantity as a parameter grows large by identifying the region of the integration domain (or the dominant term in a sum) that contributes overwhelmingly to the total — then approximate rigorously.

## Technique

Asymptotic analysis answers: "how does f(n) behave as n → ∞?" The four main tools for integral asymptotics are Laplace's method, Watson's lemma, stationary phase, and the saddle-point method. These are ordered by increasing complexity.

---

### 1. Big-O / Little-o / Asymptotic Equivalence (notation)

Before applying any method, establish notation:

- **f = O(g) as x → ∞** means ∃C, x₀ such that |f(x)| ≤ C|g(x)| for x > x₀. An upper bound on growth rate.
- **f = o(g)** means f(x)/g(x) → 0. f grows strictly slower than g.
- **f ~ g** means f(x)/g(x) → 1. Asymptotic equivalence — the strongest useful statement.
- **Asymptotic series:** f(x) ~ Σ aₙ/xⁿ means the partial sums are increasingly accurate approximations in the sense that the error is O(1/x^{N+1}). Such series often diverge — they are tools for approximation, not convergence.

**Principle:** establish which of these claims you are making before computing. "f ~ g" is much stronger than "f = O(g)" and requires more work.

---

### 2. Laplace's Method (real integrals)

**Setup:** Estimate I(λ) = ∫_a^b f(t) e^{λφ(t)} dt as λ → +∞, where φ has a unique global maximum at an interior point t₀ ∈ (a,b) with φ''(t₀) < 0.

**Key insight:** As λ → ∞, the integrand is dominated by a neighborhood of t₀. Contributions from t ≠ t₀ are exponentially suppressed.

**Leading-order formula:**
```
I(λ) ~ f(t₀) · e^{λφ(t₀)} · √(2π / (λ|φ''(t₀)|))   as λ → ∞
```

**Steps:**
1. Find the global maximum t₀ of φ on [a,b]. If the maximum is at an endpoint, the formula changes (pick up half the Gaussian).
2. Verify φ''(t₀) < 0 (strict maximum).
3. Expand φ(t) = φ(t₀) + ½φ''(t₀)(t−t₀)² + O((t−t₀)³).
4. Replace the integration limits with (−∞, +∞) (error is exponentially small).
5. Evaluate the resulting Gaussian integral.

**Deriving Stirling's formula:** Apply Laplace's method to n! = ∫₀^∞ tⁿ e^{−t} dt = ∫₀^∞ e^{n ln t − t} dt. Change variables t = ns, get e^{n(ln n − 1)} · n · Gaussian integral. Result: n! ~ √(2πn) · (n/e)^n.

**Higher-order corrections:** Expand φ and f to higher order to get the full asymptotic series. Each additional term adds a factor of 1/λ.

---

### 3. Watson's Lemma (Laplace transform asymptotics)

**Setup:** Estimate I(λ) = ∫₀^∞ f(t) e^{−λt} dt as λ → +∞, where f(t) ~ Σ aₙ t^{αₙ} as t → 0⁺ (with αₙ → ∞).

**Theorem (Watson):** Under mild conditions, the asymptotic expansion is obtained by integrating term by term:
```
I(λ) ~ Σ aₙ · Γ(αₙ + 1) / λ^{αₙ+1}
```

**Use case:** Asymptotics of Laplace transforms, and reducing Laplace's method to Watson's lemma via substitution u = φ(t₀) − φ(t).

---

### 4. Method of Stationary Phase (oscillatory integrals)

**Setup:** Estimate I(λ) = ∫_a^b f(t) e^{iλψ(t)} dt as λ → +∞, where ψ is real-valued (oscillatory, not exponentially growing).

**Key insight:** Away from stationary points (where ψ'(t) = 0), rapid oscillation causes cancellation. The dominant contribution comes from neighborhoods of stationary points t₀ where ψ'(t₀) = 0.

**Leading-order formula** (interior stationary point ψ''(t₀) ≠ 0):
```
I(λ) ~ f(t₀) · e^{iλψ(t₀)} · √(2π / (λ|ψ''(t₀)|)) · e^{±iπ/4}
```
where the sign in e^{±iπ/4} matches the sign of ψ''(t₀) (+ if ψ'' > 0, − if ψ'' < 0).

**Applications:** Asymptotics of Fourier transforms, Bessel functions, wave propagation.

---

### 5. Saddle-Point Method (complex contour integrals)

**Setup:** Estimate I(λ) = ∮_C f(z) e^{λφ(z)} dz as λ → +∞, where φ is analytic and the contour C can be deformed.

**Steps:**
1. Find saddle points: solutions to φ'(z₀) = 0.
2. Deform the contour C to pass through z₀ in the direction of steepest descent — the direction where Re(φ) decreases most steeply (this is automatically perpendicular to the steepest ascent direction, since φ is harmonic).
3. Along the steepest descent path, Im(φ) is constant (no oscillation), and the integrand decays rapidly away from z₀. The integral reduces to a Laplace-type integral.
4. Apply Laplace's method to the result.

**The Gaussian integral around the saddle:** Write φ(z) = φ(z₀) + ½φ''(z₀)(z−z₀)² + ... and evaluate:
```
I(λ) ~ f(z₀) · e^{λφ(z₀)} · √(2π / (λ|φ''(z₀)|)) · (phase factor from contour direction)
```

**Key application:** Asymptotics of central binomial coefficients, partition function asymptotics, generating function coefficient extraction (transfer matrix method via Cauchy integral + saddle point).

---

### 6. Asymptotic Expansions for Sums

- **Euler-Maclaurin formula:** Converts a sum Σ f(k) to an integral plus correction terms involving Bernoulli numbers and derivatives of f. Useful for Σ_{k=1}^n f(k) as n → ∞.
- **Transfer lemma (singularity analysis):** For generating functions f(z) = Σ aₙ zⁿ, the asymptotics of aₙ are determined by the singularities of f nearest the origin. A pole of order r at z = ρ contributes ~ C · nʳ⁻¹ · ρ⁻ⁿ.
- **Tauberian theorems:** Relate asymptotics of a series to asymptotics of the partial sums under regularity conditions.

---

### 7. Practical checklist

Before applying any method:
1. **Identify the dominant region.** Where does the integrand/summand achieve its largest value? That region controls the asymptotics.
2. **Check for multiple contributing regions.** If φ has two global maxima of equal height, both contribute and their Gaussian contributions may interfere.
3. **Match the method to the integrand type:**
   - Real exponential, smooth maximum → Laplace's method
   - Laplace transform form → Watson's lemma
   - Oscillatory with stationary phase → stationary phase
   - Complex contour with analytic integrand → saddle point
4. **Verify error bounds.** State explicitly what the error term is (O(1/λ), exponentially small, etc.).
5. **Check the formula against known special cases.** Stirling for n!, asymptotic Bessel for large argument, etc.

## Application

When asked to find the asymptotics of a quantity I(n) as n → ∞:

1. Write I(n) explicitly as an integral or sum (use integral representations when needed: gamma function, Cauchy integral formula, generating functions).
2. Identify which asymptotic regime applies (Laplace, stationary phase, saddle point, or combinatorial).
3. Locate the dominant saddle point / maximum / stationary point.
4. Apply the appropriate formula, including the prefactor (the Gaussian integral gives the √(2π/nφ'') factor).
5. State the result as f(n) ~ C · g(n) with explicit C, and specify the size of the first-order correction.
6. Cross-check with numerical computation for n = 10, 100, 1000 to verify the formula is correct before claiming it as a result.
