# credit-spread-slope-signal

When analyzing credit markets for equity regime signals, decompose the spread curve (AAA → BBB → BB → CCC) rather than tracking any single spread in isolation. The **slope** of the credit curve reveals quality rotation before headline spreads move.

**Source**: Corporate bond literature + Tetra gap analysis (2026-03-17)
**Tags**: analyst, credit, spreads, regime-detection, cross-asset

---

## The Signal: Credit Curve Steepness

The credit quality spectrum (AAA, BBB, BB, CCC) forms a "curve" analogous to the yield curve. When this curve steepens (CCC widens faster than AAA), the market is aggressively differentiating credit quality — a risk-off precursor.

### Why This Beats Single-Spread Monitoring

Watching HY spread alone (BAMLH0A0HYM2) misses the **composition** of the move:
- **Parallel widening** (all spreads widen equally): repricing of risk-free rate or general liquidity — often temporary
- **Steepening** (CCC widens 3x faster than AAA): market is pricing specific default risk — precedes equity drawdowns by 1-3 weeks
- **Flattening from below** (CCC tightens toward BBB): yield-chasing / complacency — often precedes the next blow-up

### Key Ratios

1. **CCC/BBB ratio** = `BAMLH0A3HYC / BAMLC0A4CBBB`
   - Normal: 2.5-4.0x
   - Stressed: >5.0x (quality flight)
   - Euphoric: <2.0x (no differentiation — danger)

2. **Slope z-score** = z-score of CCC-AAA differential over 60 days
   - z > 2.0: active flight to quality — weight macro risk signals higher
   - z < -1.5: yield-chasing complacency — flag as contrarian risk

3. **Rate of change matters more than level**: `dSlope/dt` is the signal. A slope that was flat and suddenly steepens 2 standard deviations in 5 days is more actionable than a persistently steep slope.

### How to Use in Tetra

- **Factor system**: Compute `credit.slope_z60`, `credit.ccc_bbb_ratio`, `credit.slope_momentum_5d` as `__macro__` factors
- **Regime HMM**: Add slope z-score as a feature — can't be in "calm" regime if credit curve is steepening aggressively
- **Debate context**: Analyst A (macro) should see slope decomposition, not just headline spread
- **Meta-signal weighting**: When slope steepens, upweight macro + credit signals, downweight momentum

### What This Skill Doesn't Do

- Cannot tell you *which* companies will default — that requires CDS-level data
- Doesn't account for technicals (ETF flows, CLO demand) that can temporarily compress spreads
- Lag: FRED data has 1-day delay; for intraday credit stress, need Bloomberg or ICE feeds
