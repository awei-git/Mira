# funding-rate-regime-signal

When analyzing crypto perpetual futures (HyperLiquid), use aggregate funding rate extremity as a positioning regime indicator. Extreme funding rates predict directional reversals and cross-asset correlation spikes.

**Source**: HyperLiquid data integration + crypto derivatives research (2026-03-17)
**Tags**: analyst, crypto, derivatives, regime-detection, hyperliquid

---

## The Signal: Funding Rate as Crowd Positioning Gauge

Perpetual futures funding rates are the price of leverage. When longs dominate, they pay shorts (positive funding). When shorts dominate, shorts pay longs (negative funding). The aggregate funding rate across top perps is a direct measurement of crowd positioning.

### Why Funding Rates Are Unique

Unlike sentiment surveys or put/call ratios, funding rates are **money on the table** — traders are literally paying to hold their positions. This makes them more reliable than opinion-based indicators:
- Survey says "bullish" → might be cheap talk
- Funding rate at 0.1%/8h → traders are paying 36.5% annualized to stay long — real conviction

### Regime Classification

Compute average funding rate across top 15 coins by OI (weighted by OI):

| Avg Funding (per 8h) | Annualized | Regime | Signal |
|---|---|---|---|
| > 0.05% | > 60% | **Euphoria** | Liquidation cascade imminent — short bias |
| 0.01% - 0.05% | 12-60% | **Bullish** | Trend following works, but watch for extremes |
| -0.01% to 0.01% | ±12% | **Neutral** | No directional signal from positioning |
| -0.05% to -0.01% | -60% to -12% | **Bearish** | Shorts crowded — watch for squeeze |
| < -0.05% | < -60% | **Capitulation** | Max fear — contrarian long signal |

### Critical Derived Signals

1. **Funding rate z-score** (vs 30-day rolling mean): z > 2.0 = crowded, expect reversion within 3-7 days
2. **OI × Funding divergence**: Rising OI + extreme funding = fragile positioning. Falling OI + extreme funding = exits in progress (less dangerous)
3. **Cross-coin funding convergence**: When BTC, ETH, SOL all show extreme positive funding simultaneously, the correlation of a liquidation event approaches 1.0 — this is systemic, not coin-specific
4. **Funding-spot basis**: If funding is positive but spot is flat/declining, longs are underwater and vulnerable

### Cross-Asset Implication

When crypto funding is in Euphoria regime:
- Crypto-equity correlation tends to spike in the subsequent drawdown
- Risk-off moves hit both crypto AND growth equities (ARKK, NVDA correlation with BTC > 0.6)
- Signal for Tetra: downweight momentum factor in growth equities when crypto funding > 0.05%

### How to Use in Tetra

- **Factor pipeline**: `hl.avg_funding_rate`, `hl.funding_rate_z` already computed as `__macro__` factors
- **Analyst C (crowd)**: receives HyperLiquid data — tell LLM to interpret funding extremes as positioning risk
- **Meta-signal layer**: when HL funding regime = Euphoria, increase weight on macro/credit signals, decrease momentum
- **Scenario generation**: extreme funding → add "crypto liquidation cascade" scenario with equity contagion estimate

### Boundary Conditions

- Funding rates are reliable for top-15 coins; low-liquidity coins have noisy funding
- New listings often have extreme funding that's not crowding — filter by minimum 7 days of trading history
- Funding is paid every 8 hours on HyperLiquid — snapshot timing matters. Use the latest snapshot, not daily average
