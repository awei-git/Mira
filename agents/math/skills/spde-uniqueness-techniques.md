# SPDE Uniqueness and Regularity: Working Procedure

**Trigger if:** You have an SPDE of the form $$dv = \frac{1}{2}\Delta v \, dt + f(v, \nabla v) \nabla v \, dF$$ with Gaussian noise F, and you need to prove:
1. **Existence** when standard Picard iteration fails (non-Lipschitz coefficients or rough noise)
2. **Uniqueness** when coefficients are Hölder continuous (α < 1) or noise is fractional
3. **Density regularity** for the law of u(t,x) when noise is non-Brownian

## Step 0: Noise Viability Check

1. Compute spectral measure μ of Σ(x,y) = min(s,t) σ(|x−y|).
2. Verify Dalang's condition: ∫ μ(dξ)/(1+|ξ|²) < ∞.
   - **Fails in d ≥ 2?** Need spatially colored noise (σ must decay). Reformulate with smoother covariance or reduce dimension.
   - **Passes?** Proceed to existence/uniqueness.
3. If noise is fractional BM (H ≠ 1/2): flag this. Malliavin density arguments will break at the blocking step (see §Density below).

## Decision: What Are You Trying to Prove?

### → Strong Uniqueness

**Are coefficients Lipschitz?**
- **Yes:** Standard Gronwall on E[‖u−v‖²]. Done.
- **No (Hölder, α < 1):** Use Yamada-Watanabe.

**Yamada-Watanabe — concrete steps:**
1. Build smooth convex φ_n with: φ_n(0)=0, φ_n′ bounded by 1, φ_n″(x) ≤ 2/(n|x|), and φ_n(x) ↑ |x|.
2. Apply Itô's formula to φ_n(X_t − Y_t) for two candidate solutions X, Y.
3. The φ_n″ term produces a local-time integral. Bound it using: E[∫ L^a_t φ_n″(a) da] where L^a is the local time of X−Y at level a.
4. Show this bound → 0 as n → ∞. **This is where proofs stall.** You need: (a) the diffusion coefficient satisfies |σ(x)−σ(y)| ≤ ρ(|x−y|) with ∫₀ ρ⁻²(u) du = ∞, and (b) enough integrability to exchange limit and expectation.
5. Conclude E[|X_t − Y_t|] = 0 a.s.

**Can't get pathwise estimates at all?** Switch to BDSDE.

**BDSDE approach — concrete steps:**
1. Write the SPDE solution as u(t,x) = Y_t where (Y,Z) solves a BDSDE with both forward integral (dW) and backward integral (dB).
2. The backward integral absorbs the spatial noise. Uniqueness of (Y,Z) gives uniqueness of u.
3. For the BDSDE uniqueness: verify the generator g satisfies |g(t,y,z) − g(t,y′,z′)| ≤ C|y−y′| + α_t|z−z′| with α ∈ L² (Pardoux-Peng conditions).
4. If g is only monotone (not Lipschitz in y): use comparison theorem — show Y ≤ Y′ when terminal conditions are ordered.

**When to prefer BDSDE over Yamada-Watanabe:** When the nonlinearity is in the drift f(v,∇v) rather than the diffusion coefficient, or when the noise structure makes Itô formula on the difference process intractable.

### → Existence

1. **A priori energy estimate:** Multiply SPDE by v, integrate. Get E[sup_t ‖v‖²_H + ∫₀ᵀ ‖∇v‖² dt] ≤ C. If this blows up, the nonlinearity is too strong — regularize f.
2. **Galerkin approximation:** Project onto span{e₁,…,e_n} (eigenfunctions of Δ). Solve the finite-dimensional SDE system.
3. **Tightness:** Show laws of {v_n} are tight in C([0,T]; H) ∩ L²([0,T]; V). Use Aldous criterion or the compact embedding V ⊂⊂ H ⊂ V*.
4. **Identify the limit:** Extract weakly convergent subsequence. Show it solves the martingale problem. **Common failure:** the nonlinear term f(v_n, ∇v_n)∇v_n doesn't pass to the limit. Fix: get strong convergence of ∇v_n via Lions-Aubin compactness.
5. **Combine with uniqueness** (above) to upgrade weak solution to strong.

### → Density Regularity (smoothness of law of u(t,x))

**Standard noise (Brownian):**
1. Compute Malliavin derivative D_s u(t,x) by differentiating the mild formulation.
2. D_s u(t,x) solves a linearized SPDE. Write it via the Green's function: D_s u(t,x) = G(t−s, x−·) σ(u(s,·)) + ∫_s^t G(t−r,x−·)(∇σ · D_s u)(r,·) dr.
3. Show the Malliavin matrix γ = ∫₀ᵗ |D_s u(t,x)|² ds is a.s. positive.
4. Prove γ⁻¹ ∈ Lᵖ for all p. Then u(t,x) has a smooth density by Bouleau-Hirsch criterion.

**Fractional BM (H ≠ 1/2):** Step 3 breaks. The blocking argument (partitioning the integral into independent pieces) fails because increments are correlated.
- **Workaround for H > 1/2:** Transfer to a Volterra representation, work with the underlying Wiener process, pay for it with kernel estimates.
- **H < 1/2:** Open problem for most nonlinear SPDEs. Don't spend time here without new ideas.

## When You're Stuck

| Symptom | Likely cause | Fix |
|---|---|---|
| Energy estimate diverges | Growth of f(v,∇v) too fast | Truncate f at level R, solve, then show R → ∞ limit exists |
| Tightness fails | Missing spatial regularity | Strengthen the covariance assumption on Σ; check Dalang |
| Can't pass nonlinearity to limit | Only weak convergence of ∇v_n | Need strong compactness — verify Lions-Aubin hypotheses |
| Malliavin matrix degenerates | Noise is too smooth or degenerate | Check: does σ(u) vanish? If so, density may genuinely not exist at that point |
| Yamada-Watanabe local time bound won't close | ρ integrability condition fails | Coefficient is too irregular for this method. Try BDSDE or regularization |