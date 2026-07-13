---
activation_trigger: "Apply when building, reviewing, or stress-testing a DCF model to derive an intrinsic value or price target for a security."
---

# Intrinsic Valuation (DCF)

**Tags:** analyst, finance, valuation, dcf, equity

**Triggers:** When you need to *build* a DCF model from scratch; When you must *sanity-check* a third-party DCF valuation; When *preparing materials* for an investment committee review; When *critically reviewing* a colleague's or advisor's DCF model; When *setting a specific price target* for a security; When a DCF yields an *absurd result* (e.g., negative EV, 100x implied multiple) and you need to debug it; When *deciding* whether a DCF is the appropriate valuation tool for a given company.

## Core Principle
A DCF is a structured argument about the future, not a calculation. Build it as follows:
1.  **Make each input claim explicit.** Define every assumption (growth, margins, capex, discount rate).
2.  **Defend each claim.** Anchor it to a business driver, historical evidence, or competitive analysis.
3.  **Show what happens when a claim is wrong.** Stress-test key assumptions to reveal the model's sensitivity.

## When to Skip the DCF Entirely
A DCF adds nothing when you can't bound the key inputs within a useful range. Don't build one for:
- **Pre-revenue or optionality-driven businesses.** If the value is in the right tail of outcomes (biotech with Phase 2 assets, early-stage platforms), a probability-weighted scenario model or option pricing is the right tool. A DCF will force you to pick a single path and then hide the option value inside an inflated terminal growth rate.
- **Financial institutions.** FCF is meaningless when leverage IS the business. Use excess return / residual income models.
- **When your variant perception is on a single binary event** (regulatory approval, contract win). A scenario tree with probabilities is more honest. A DCF will bury the binary in a blended growth rate that nobody can interrogate.

If you catch yourself using a DCF as a container for a thesis that doesn't need one, stop.

## Reviewing Someone Else's DCF — First 5 Minutes
Before reading any line item, check these three things. They catch 80% of broken models:
1.  **Terminal value as % of total.** If >80%, the explicit forecast is decorative. The model is a terminal value guess wearing a spreadsheet costume.
2.  **Implied exit multiple.** Back into it from the Gordon Growth terminal value. If the implied EV/EBITDA is 25x for a mature industrial company, something is wrong upstream — usually terminal growth is too high or terminal margins are too rich.
3.  **Revenue CAGR vs. TAM.** If Year 10 revenue implies 40% market share in a market the company currently has 8% of, that's not a forecast, it's a wish. Check whether the model ever explains *how* the share gain happens.

If all three pass, then read the details. If any fail, start there — everything else is downstream of a broken assumption.

## Building a DCF — Decisions That Matter

### 1. Project FCF (5-10 year explicit period)
- **How long?** Until the business reaches steady state. If you can't articulate why year 7 looks different from year 5, use 5.
- **Revenue:** Anchor to a driver (units × price, subscribers × ARPU, same-store + new store), never top-line growth rates alone. Unanchored growth rates drift optimistic — a "conservative" 12% CAGR compounding for 8 years triples revenue. Does the market support that?
- **Margins:** Model the *path*, not just the endpoint. "30% operating margin by year 5" needs a story for each year. Where do the 200bps come from in year 3? Procurement? Mix shift? Pricing power? If you can't name the source, you're extrapolating.
- **Capex:** Only maintenance capex belongs in the terminal year. A common tell for a broken model: growth capex running at 8% of revenue in the explicit period, then silently dropping to 3% in the terminal year with no explanation.
- **SBC in FCF:** Decide once and be consistent. Either subtract SBC from FCF and use basic share count, or leave it in FCF and use fully diluted shares. The most common error is doing neither — ignoring SBC in FCF *and* using basic shares. On a tech company with 5-8% annual dilution, this inflates equity value by 30%+. Check which convention the model uses before reading any output.
- **Trap — current margins on a transitioning business.** A company investing heavily in a new segment will have depressed margins now and higher margins later (or vice versa). Projecting today's 14% margin forward when management is guiding to 22% post-transition — or blindly accepting the 22% — are both wrong. What's the evidence for the transition succeeding?
- **Debugging absurd outputs:** If your DCF yields a nonsensical value (e.g., negative enterprise value, implausibly high multiples), follow this: 1) Check for a sign error in FCF (e.g., positive capex added instead of subtracted). 2) Verify the discount rate is a percentage (0.10) not a whole number (10). 3) Ensure terminal value formula references the *first* year of perpetuity, not the last year of the explicit period.

### 2. Set the Discount Rate
- **The real decision is beta.** Regression beta is backward-looking and noisy for small-caps, recent IPOs, or companies mid-pivot. Use sector unlevered beta re-levered to target capital structure when the company's own history is unreliable.
- **Trap — false precision.** If your valuation swings 15%+ on 40bps of WACC, the model is telling you it doesn't know the answer. Widen your range instead of debating 9.3% vs 9.7%. The WACC is the least defensible number in the model — spend your time on the cash flows.
- **Country risk:** For emerging-market cash flows, add a country risk premium or use local-currency risk-free rates. But don't stack sovereign CDS spread on top of an already-elevated ERP — that's double-counting.

### 3. Terminal Value — Where Most of the Value Lives
- **Terminal growth rate sanity check:** If your terminal rate exceeds long-run nominal GDP (~2-4% developed markets), you're claiming the company outgrows the economy forever. The question isn't whether the rule is right — it's what specific reinvestment rate and ROIC your terminal growth rate implies. A 4% growth rate at a 10% ROIC means 40% of NOPAT reinvested in perpetuity. Does that match the business?
- **Cross-check:** Calculate terminal value via BOTH Gordon Growth and exit multiple. If they diverge >20%, one of your assumptions is inconsistent. Common cause: terminal margin assumption implies a growth profile that doesn't match your terminal growth rate.
- **Exit multiple:** Use current sector median, not the company's own current multiple. Using today's 30x multiple as your exit multiple assumes today's valuation is correct — which is the thing you're trying to determine.

### 4. Bridge to Equity Value — Checklist
This is where sloppy models hide 10-15% of error. Walk through each item:
- [ ] Mid-year convention applied (operating business, not liquidation)
- [ ] Net debt at market value if debt trades; book value otherwise
- [ ] Pension/OPEB deficits included (check 10-K footnotes, not balance sheet)
- [ ] Operating leases: already capitalized under IFRS 16/ASC 842? Don't subtract again
- [ ] Minority interests subtracted at fair value, not book
- [ ] Equity method investments: added back if excluded from FCF projections
- [ ] Cash: is it truly available? Trapped cash (overseas pre-repatriation, regulatory minimums) should be haircut or excluded

### 5. Stress-Test and Present
- **Sensitivity table is mandatory.** Minimum: 2D table on terminal growth rate × WACC. Add a third axis (margin or revenue growth) if those are contested inputs.
- **Scenario discipline:** Run a case where your most optimistic assumption is wrong. Does the investment still work at that price? That's your margin of safety.
- **Present as a range** (bear / base / bull), never a single number. Lead with: "This valuation is most sensitive to [X] and [Y]. If you disagree on those, here's how the number moves."
- If terminal value >80% of total, say so explicitly and widen the range. You're making a bet on steady state, not on your forecast.

## Cross-Validation Checks
- **Comps:** If your DCF implies 25x EV/EBITDA for a company whose peers trade at 12-16x, either you've found genuine alpha or your model has a buried assumption. Check which single assumption, removed, brings you back inside the peer range — that's the assumption carrying your entire thesis.
- **Precedent transactions:** Adjust for control premiums (typically 20-40%). A take-private at 18x doesn't mean the public equity is worth 18x.
- **Reverse DCF:** What growth rate does the current stock price imply? If the market is pricing in 15% revenue CAGR and you think it's 10%, you've found your variant perception without building a full model. This is often the fastest way to frame an investment debate: "the stock is priced for X, we believe Y, here's why."

## Where Models Break
- **Consensus-as-base-case:** Using Street estimates for your projections means you're modeling the market's existing view, not your own. Your DCF only adds value where your assumptions *differ* from consensus. If they don't differ anywhere, you don't have a view.
- **Nominal/real mismatch:** Inflation embedded in revenue growth + real risk-free rate in WACC = everything downstream is wrong. Pick one convention and be consistent.
- **Survivorship bias in comps:** Selecting only successful peers inflates multiples. Include the ones that struggled — they're part of the distribution your company might follow.
- **Circular references:** If you're modeling interest expense as a function of debt, which is a function of enterprise value, which depends on WACC, which depends on cost of debt — break the loop. Fix the debt schedule or iterate to convergence. Excel's iterative calculation setting masks this; a model that needs it turned on has a circularity you should understand and explicitly manage.
