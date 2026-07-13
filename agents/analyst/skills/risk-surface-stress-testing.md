---
activation_trigger: "Apply when stress-testing a thesis or portfolio against historical, hypothetical, and reverse-stress scenarios to identify hidden tail risks."
---

# Risk Surface Stress Testing

**Tags:** analyst, risk, stress-testing, portfolio, tail-risk

## Core Principle
Systematically stress a thesis or portfolio against tail scenarios to identify hidden concentrations and underpriced risks before they materialize.

## Define the Risk Surface
Enumerate all major risk factors that could affect the position: interest rates, FX, commodity prices, credit spreads, regulatory events, key person risk, geopolitical shocks.

## Three Types of Stress Scenarios
1. **Historical stress** — replicate known crises: 2008 GFC, 2020 COVID crash, 1994 bond massacre, 1997 Asian crisis. Use actual market moves, not sanitized assumptions.
2. **Hypothetical stress** — custom narrative shocks specific to the thesis: "What if the key regulatory approval is denied? What if the largest customer churns?"
3. **Reverse stress** — ask: "What scenario would destroy this thesis?" Work backwards from the break-point.

## Rules
- Shock correlated variables **simultaneously** — not in isolation. Correlated shocks reveal concentrations that single-factor analysis misses.
- Use Expected Shortfall (CVaR at 95th/99th percentile), not just VaR — tail losses are what matter for survival.
- Find the **scenario killer**: which single scenario causes permanent loss of capital (not just mark-to-market)?
- Size positions proportionally to downside discovered in stress tests, not just to expected return.
- Re-run after any major portfolio change or macro regime shift.

## Source
FRM Part 1 stress testing curriculum (GARP); Baringa tail risk management framework; BlackRock Aladdin risk methodology; Nassim Taleb "Antifragile"
