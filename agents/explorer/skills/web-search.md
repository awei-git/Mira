---
activation_trigger: "Apply when conducting web research and needing to formulate progressively constrained queries and interpret result quality across multiple search strategies."
---

# Web Search — Query Formulation and Result Interpretation

**Tags:** explorer, web-search, research, duckduckgo, query-design

## Core Principle
Web search quality is 80% query design and 20% result parsing. A precise query eliminates an entire category of irrelevant results before they appear. Search is not lookup — it is progressive constraint application.

## Query Formulation Hierarchy

**Level 1 — Too broad (never start here):**
> "AI agents"

**Level 2 — Topic-scoped:**
> "AI agent memory architecture 2025"

**Level 3 — Claim-targeted (preferred):**
> "AI agent working memory limitations context window empirical"

**Level 4 — Source-targeted:**
> `site:arxiv.org "agent memory" architecture evaluation 2025`

Move from Level 2 → 3 → 4 when earlier results are too general.

## Operator Toolkit

| Operator | Effect | Example |
|----------|--------|---------|
| `"..."` | Exact phrase | `"context window" limitation` |
| `site:X` | Restrict to domain | `site:github.com` |
| `-X` | Exclude term | `AI agent -chatbot -chatgpt` |
| `filetype:pdf` | PDFs only | `filetype:pdf transformer architecture` |
| `intitle:X` | Term in page title | `intitle:"benchmark" LLM evaluation` |
| `after:YYYY-MM-DD` | Recency filter | `after:2025-01-01` |

## Result Quality Signals

**Trust more:**
- Domain authority: arxiv.org, scholar.google, github.com, official docs, major academic publishers
- Results with specific claims, numbers, dates — not vague generalities
- Author-attributed content (named researcher/engineer)

**Trust less:**
- SEO-optimized content farms (listicles, "X things you need to know")
- Summary-of-summary sites (re-hashing secondary sources)
- Content without clear publication date or author
- Top 3 results are all from the same domain (result set lacks diversity)

## Query Strategies for Common Tasks

**Finding recent developments:**
> `"[technology]" news -tutorial -guide after:2025-01-01 site:news.ycombinator.com OR site:arxiv.org`

**Finding implementation examples:**
> `"[technology]" example implementation github.com OR huggingface.co`

**Fact-checking a specific claim:**
> `"[exact claim phrase]" -[source making claim]` — look for independent confirmation or refutation

**Finding the primary source:**
> `"[quote or finding]" source OR paper OR study -blog -opinion`

## When to Switch Engines
- DuckDuckGo: default, privacy-respecting, good for technical queries
- Bing: better for recent news, image search, structured data
- Google Scholar: for academic literature depth (use `scholar.google.com` via site: operator)
- If first 5 results are ads or thin content: reformulate at Level 3/4 before trying another engine

## Source
Standard information retrieval principles; DuckDuckGo operator documentation; practitioner web research methodology.
