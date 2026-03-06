# Quantitative Reasoning

**Tags:** market-analysis, market-sizing, unit-economics, quantitative, financial-analysis

## Core Principle
Ground every market claim in numbers by building estimates from first principles -- size the market in layers (TAM/SAM/SOM), validate with unit economics, and use cohort analysis to distinguish real growth from vanity metrics.

## Technique

### 1. Market Sizing: TAM / SAM / SOM

**Total Addressable Market (TAM)** -- the entire revenue opportunity if you captured 100% of the relevant market globally. Two approaches:

- **Top-down:** Start with a broad industry figure and narrow. E.g., "Global SaaS market is $200B; HR SaaS is ~8% = $16B TAM." Fast but imprecise. Good for sanity checks.
- **Bottom-up:** Count potential customers x willingness to pay. E.g., "500K companies with 50-500 employees x $20K average annual contract = $10B TAM." Slower but more defensible. Always preferred when data is available.

When top-down and bottom-up estimates diverge significantly, investigate why. The gap often reveals a flawed assumption.

**Serviceable Addressable Market (SAM)** -- the portion of TAM you can realistically reach given your product scope, geography, and go-to-market. Apply concrete filters: geography, language, regulatory access, product fit.

**Serviceable Obtainable Market (SOM)** -- realistic near-term capture based on current resources, competitive dynamics, and go-to-market capacity. Typically 1-5% of SAM for early-stage; higher for established players in fragmented markets.

### 2. Unit Economics

The fundamental health check for any business model:

- **Customer Acquisition Cost (CAC)** -- total sales & marketing spend / new customers acquired. Break down by channel.
- **Lifetime Value (LTV)** -- average revenue per customer x gross margin x average customer lifespan. For subscription: (ARPU x gross margin) / churn rate.
- **LTV:CAC ratio** -- healthy businesses target 3:1 or higher. Below 1:1 means the business destroys value with every customer acquired.
- **Payback period** -- months to recover CAC from gross profit. Under 12 months is strong; over 18 months creates cash flow strain.
- **Contribution margin** -- revenue minus all variable costs per unit. The building block of profitability analysis.

When analyzing a market or competitor, estimate their unit economics even from incomplete data. Public companies disclose enough to approximate. For private companies, use industry benchmarks and triangulate from hiring patterns, pricing, and growth rate.

### 3. Growth Rate Analysis

Raw growth numbers mislead. Decompose growth:

- **Organic vs. inorganic** -- strip out acquisitions to see true organic growth.
- **New customer vs. expansion** -- is growth coming from acquiring new customers or expanding within existing ones? Expansion-driven growth is typically more durable.
- **Absolute vs. percentage** -- a company growing 100% from $1M to $2M is very different from one growing 20% from $500M to $600M. Always contextualize rates with absolute numbers.
- **Cohort retention curves** -- the single most important growth diagnostic. Plot revenue or usage by cohort over time. Healthy: curves flatten (retention). Unhealthy: curves trend toward zero (churn). A business growing topline while cohorts decay is filling a leaky bucket.

### 4. Cohort Analysis Fundamentals

Group customers by acquisition period (month or quarter) and track:
- **Retention rate** -- % still active at month 1, 3, 6, 12.
- **Revenue retention** -- can exceed 100% if expansion revenue outpaces churn (net revenue retention).
- **Engagement curves** -- usage intensity over time by cohort.

Key patterns:
- **Smile curve** -- retention dips then recovers. Indicates initial friction but eventual habit formation.
- **Flat curve** -- retention stabilizes at a level. Indicates product-market fit for a segment.
- **Decay curve** -- continuous decline. Indicates weak retention and potential product-market fit issues.

### 5. Sanity Checks

Always cross-check estimates:
- Does the implied market share make sense given competitive dynamics?
- Does the growth rate imply a customer count that exceeds the addressable population?
- Do the unit economics support the valuation or funding round?
- Is the implied penetration rate consistent with the product's maturity?

When a number fails a sanity check, flag it explicitly. State what assumption would need to be true for the number to hold.

## Application

When performing quantitative market analysis:
1. Size the market using both top-down and bottom-up methods. Reconcile any divergence.
2. Estimate unit economics for the key players or the business being analyzed. Flag where you are estimating vs. using reported data.
3. Decompose growth rates -- never report a single topline growth number without context.
4. If customer-level data is available or estimable, build cohort retention curves.
5. Run sanity checks on every major number. State assumptions explicitly.
6. Present ranges, not false precision. "TAM is $8-12B" is more honest than "TAM is $9.7B" when the inputs are estimates.

Every number should have a source or a stated assumption. Unsourced numbers are opinions, not analysis.
