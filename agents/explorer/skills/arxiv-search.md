# Arxiv Search — Explorer Feed Strategy

**Tags:** explorer, arxiv, research, papers, AI, signal-detection

## Core Principle
Arxiv is a preprint server — papers appear before peer review. The signal is *direction of research attention*, not *validated findings*. Use it to track what problems researchers are actively working on, not as ground truth.

## High-Value Categories for Explorer

| Category | Focus area |
|----------|------------|
| `cs.AI` | Broad AI — agents, planning, reasoning |
| `cs.LG` | Machine learning — training, architectures, optimization |
| `cs.CL` | NLP, LLMs, language models |
| `cs.CV` | Computer vision, multimodal |
| `cs.RO` | Robotics — embodied AI, physical agents |
| `cs.MA` | Multi-agent systems |
| `cs.CR` | Security — adversarial, AI safety overlap |
| `stat.ML` | Statistical learning theory |
| `econ.GN` | Economics — AI economic impacts |

**Default explorer fetch:** `cs.AI + cs.LG + cs.CL`, last 24–48 hours, max 15 results.

## Fast Paper Assessment (30-second rule)

1. **Title**: Does it name a specific technique, model, or finding? Generic titles ("A Survey of X") = low priority
2. **Author count**: 1-3 authors = focused contribution; 10+ = large lab benchmark paper
3. **Abstract first sentence**: What claim is made? Is it falsifiable and specific?
4. **Abstract last sentence**: What did they actually demonstrate? "We show that..." vs "We hope to..."

## Signal Classification

**Prioritize:**
- New technique with empirical benchmark comparison (not just "our method is better" — actual numbers)
- Challenge papers: "We show X widely-believed result doesn't hold under condition Y"
- Infrastructure papers: new dataset, evaluation framework, or efficiency method
- Interdisciplinary: physics/math/economics formalism applied to ML problem

**Deprioritize:**
- Yet another fine-tuning comparison on existing benchmark
- "We achieve SOTA" without ablation studies
- Survey papers (useful but not exploratory signal)
- Papers with no code linked and vague reproducibility

## Recency Layering
- **Last 24 hours**: directional pulse — what's new today
- **Last 7 days**: confirmed emerging threads — multiple papers on same sub-topic = real trend
- **Last 30 days**: structural shifts — new problem categories being formally named

## Cross-Source Integration
- arxiv paper → corresponding GitHub repo in fetcher → check stars/adoption
- arxiv paper → related HN discussion → read comments for practitioner reaction
- arxiv paper cited in briefing → store in memory for reading-list follow-up

## Output Format
For each paper surfaced in a briefing:
```
[arxiv:ID] Title (Authors, Year)
Finding: One-sentence core claim.
Significance: Why it matters — what does it change or challenge?
```

## Source
Arxiv API (`export.arxiv.org/api/query`); arxiv category taxonomy; paper triage methodology from academic reading practice.
