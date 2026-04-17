---
activation_trigger: "Apply when classifying the current macroeconomic regime (Goldilocks/Reflation/Stagflation/Deflation) to guide asset allocation decisions."
---

# Macro Regime Mapping

**Tags:** analyst, macro, investing, regime, asset-allocation

**When to use:** 1) Monthly review (first business day). 2) Adding a new position >5% of portfolio. 3) ISM PMI, Core CPI (3m a), or Fed Funds surprise >1.2σ from consensus. 4) IG OAS moves >50bps in a month. 5) Any Step 6 tripwire fires.

## The Four Regimes
| Regime | Growth | Inflation | Favored Assets | Median Duration |
|--------|--------|-----------|----------------|----------------|
| Goldilocks | Rising | Falling | Equities, Credit | 18-24 months |
| Reflation | Rising | Rising | Commodities, TIPS, Energy | 12-18 months |
| Stagflation | Falling | Rising | Cash, Short Duration, Real Assets | 6-12 months |
| Deflation | Falling | Falling | Long Bonds, Defensives, Gold | 6-18 months |
*Duration: US post-1970 approximation. Use as base rate, not prediction.*

## Execution Steps

### Step 1: Classify Current Regime (Monthly/Trigger)
1.  **Score Growth:** For each indicator, assign +1 (rising), 0 (flat), -1 (falling).
    *   ★ ISM Manufacturing PMI: >50 & 3m trend up = +1. <50 & 3m trend down = -1.
    *   Yield Curve (10y-2y): Steepening from inverted = +1. Inverting = -1.
    *   ★ Initial Jobless Claims (4-wk avg): Falling = +1. Rising >10% from trough = -1.
    *   Senior Loan Officer Survey: Net % tightening loosening = +1. Tightening = -1.
    *   **Calculate:** Sum scores, weighting ★ indicators x1.5. Positive = Rising Growth. Negative = Falling Growth. Zero = use prior month's direction.
2.  **Score Inflation:** Same method.
    *   ★ Core CPI (3m annualized): >3% & rising = +1. <2% & falling = -1.
    *   ★ 5y5y Breakeven: >2.5% & rising = +1. <2% & falling = -1.
    *   Avg Hourly Earnings (YoY): >4% = +1. <3% = -1.
    *   BCOM Index (3m chg): >5% = +1. <-5% = -1.
3.  **Map to Regime:** (Growth, Inflation) → Regime per table.

### Step 2: Assess Transition Probability
Score +1 if condition met, else 0.
*   **Growth Leads:** Real M2 YoY <0% in rising-growth regime (or >2% in falling). ISM New Orders-Inventories spread <0 in rising-growth (or >+5 in falling). Building permits trend diverges.
*   **Inflation Leads:** Unit Labor Cost (2q trend) >4% in falling-inflation regime (or <1% in rising). Import Prices (3m a) diverge by >3pp. Fed Funds vs. Taylor Rule gap widens (>50bps).
*   **Probability:** 0 triggers = <20%. 1-2 = 20-50%. 3-4 = 50-75%. 5-6 = >75%.
*   **Next Regime:** If only growth leads fire, move horizontally on table. If only inflation leads, move vertically. If both, move diagonally.

### Step 3: Determine Conviction & Sizing
1.  **Count Ambiguous Coincident Indicators:** Tally scores of '0' from Step 1.
2.  **Base Sizing:**
    *   High Conviction (0-1 ambiguous) & Transition Prob <50% → 100% sizing.
    *   High Conviction & Transition Prob ≥50% → 75% sizing.
    *   Moderate (2-3 ambiguous) & Transition Prob <50% → 65% sizing.
    *   Moderate & Transition Prob ≥50% → 40% sizing.
    *   Low (4+ ambiguous) → 30% sizing.
3.  **Apply Credit Overrides:**
    *   IG OAS widens >25bps/month → Reduce sizing by one tier (e.g., 100%→75%).
    *   Bank lending tightening + yield curve inverted → Override any Goldilocks call to Moderate conviction minimum.
    *   Spreads compress while growth indicators weaken → Cap max sizing at 65%.

### Step 4: Produce Output
```
Regime: [Goldilocks | Reflation | Stagflation | Deflation]
Conviction: [High | Moderate | Low]
Growth Score: [sum] (Disagreements: [list])
Inflation Score: [sum] (Disagreements: [list])
Transition Probability: [<20% | 20-50% | 50-75% | >75%]
Next Probable Regime: [regime]
Credit Override: [None | OAS Widening | Tightening+Inversion | Late-Cycle]
Position Sizing: [%]
Bias: [Overweight/Underweight list]
Reversal Signal: [Single key indicator]
Next Review: [Date]
```

### Step 5: Set Tripwires (Monitor Daily)
Run full procedure if ANY fire:
1.  **Primary Flip:** ★ ISM PMI or ★ Jobless Claims reverses direction vs. call.
2.  **Price Divergence:** Favored asset class underperforms unfavored by >2σ (20-day).
3.  **Narrative Gap:** Need "temporary" to explain 3-month data trend contradicting call.
4.  **Correlation Break:** Pairwise correlation within favored asset basket <0.3 (20-day).
5.  **Volatility Spike:** VIX rises >40% in a week during Goldilocks/Reflation call.
*   **If tripwire fires:** Re-run Steps 1-4. If regime changes, execute immediately. If not, note reason. Two fires of same type without change → downgrade conviction one tier.

## Sector Positioning & Monitoring
| Regime | Overweight | Entry Trigger | Underweight | Exit Trigger | Monitor |
|--------|-----------|---------------|-------------|--------------|---------|
| Goldilocks | Cyclicals (XLK, XLY, XLI) | PMI >52 & rising | Defensives, Cash | PMI <50 for 2 months | Weekly |
| Reflation | Energy (XLE), Materials (XLB), TIPS | BCOM 3m chg >5% | Long Bonds (TLT), Growth Tech | BCOM 3m chg <0% for 3m | Weekly |
| Stagflation | Cash (BIL), Short Corp (SPSB), Commodity Producers | Core CPI 3m a >4% | Long Bonds, Cyclicals | Core CPI 3m a <3% | Weekly |
| Deflation | Long Bonds (TLT), Utilities (XLU), Staples (XLP), Gold (GLD) | PMI <48 & falling | Cyclicals, Credit | PMI >50 & yield curve steepening | Weekly |

## Regime Transition Checklist
- [ ] **Confirmation:** ≥3 of 8 coincident indicators flipped direction from prior month.
- [ ] **Exception:** Transition Prob ≥75% AND ≥2 coincident indicators flipped → confirm transition.
- [ ] **Shock vs. Transition:** If coincident indicators spike but leading indicators show <20% probability, hold call for one full review cycle.
- [ ] **Positioning:** Follow Step 3 sizing table during transition.
- [ ] **Re-entry:** After exiting a regime, require full classification procedure (Steps 1-4) to re-enter.

## Sources
ISM, BLS, Fed, FRED; Bridgewater All-Weather; Dalio Debt Cycle Principles.
