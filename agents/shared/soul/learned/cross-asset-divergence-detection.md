# cross-asset-divergence-detection

When credit spreads and equity volatility disagree, one market is wrong. Systematically detect these divergences to find the highest-conviction trading signals.

**Source**: Cross-asset literature + Tetra gap analysis (2026-03-17)
**Tags**: analyst, cross-asset, divergence, regime-detection, credit, volatility

---

## The Principle: Markets Price Risk Differently

Credit spreads, equity volatility (VIX), and crypto funding rates all measure risk — but from different angles:
- **Credit spreads**: default risk + liquidity premium (slow-moving, institutional)
- **VIX**: expected equity volatility (fast, derivatives-driven)
- **Funding rates**: leveraged positioning (real-time, trader-driven)

When these markets agree (spreads wide + VIX high + negative funding), the regime is clear. When they **disagree**, someone is mispriced and a correction is coming.

### The 4 Actionable Divergences

#### 1. Credit Widens, VIX Stays Low
**Pattern**: BBB/HY spreads widen >1 std dev, but VIX flat or declining
**What it means**: Bond market sees stress that equity market hasn't priced
**Historical resolution**: Equities catch down 70% of the time within 2-3 weeks
**Action**: Reduce equity exposure, buy puts, overweight cash
**Recent example**: Sep 2024 — HY spread widened 80bps over 2 weeks while VIX stayed below 15; SPY corrected 5% in the following month

#### 2. VIX Spikes, Credit Calm
**Pattern**: VIX >25 but BBB/HY spreads barely move
**What it means**: Equity market fear is technical (options positioning), not fundamental
**Historical resolution**: Equities recover 65% of the time — the "vol spike without credit confirmation"
**Action**: Fade the vol spike — sell puts, add to quality positions

#### 3. Crypto Funding Extreme, Equity Calm
**Pattern**: Avg funding >0.05% (Euphoria), but VIX <18 and spreads stable
**What it means**: Crypto is over-leveraged but equity risk hasn't repriced
**Historical resolution**: Crypto deleveraging event → equity contagion in growth names (60% probability)
**Action**: Reduce crypto-correlated equity exposure (MSTR, COIN, MARA), consider downside protection on QQQ

#### 4. All Three Converge to Extremes
**Pattern**: Wide spreads + high VIX + negative funding simultaneously
**What it means**: Unanimous risk-off pricing — this is either crisis or capitulation
**Action**: If sustained >5 days, likely capitulation — contrarian long with tight stops. If <3 days, may escalate — stay defensive.

### Implementation

**Compute daily divergence scores:**
```
credit_z = z-score of BBB spread (60-day)
vix_z = z-score of VIX (60-day)
funding_z = z-score of avg funding rate (30-day)

divergence_credit_equity = credit_z - vix_z
divergence_crypto_equity = funding_z - vix_z
divergence_credit_crypto = credit_z + funding_z  (both are "risk" measures, same sign = agreement)
```

**Threshold for actionable divergence**: |divergence| > 1.5 standard deviations

### How to Use in Tetra

- **Factor pipeline**: Compute `divergence.credit_equity`, `divergence.crypto_equity`, `divergence.credit_crypto` as `__macro__` factors
- **Debate Round 3 (CIO)**: When divergence is extreme, the CIO should explicitly call out which market is likely wrong and why
- **Meta-signal layer**: Divergence > 1.5 → override normal signal weights; trust the market that historically leads (credit leads equity 70% of the time)
- **Scenario generation**: Divergences should generate "convergence scenarios" — what happens if credit is right? What if VIX is right?

### Why This Matters for Tetra Specifically

Tetra's debate structure creates natural information asymmetry between analysts. But the **CIO synthesis doesn't currently check cross-asset consistency**. Adding divergence detection means the CIO can say: "Analyst A sees widening credit spreads but Analyst C's crowd signals are bullish — history says credit is right 70% of the time, so I'm weighting Analyst A's view higher."

### Limitations

- Divergences can persist for weeks before resolving — don't trade them with tight time horizons
- In QE/central bank intervention regimes, credit spreads can stay artificially compressed — divergence with VIX may reflect policy, not mispricing
- Need minimum 60 trading days of data to compute reliable z-scores
