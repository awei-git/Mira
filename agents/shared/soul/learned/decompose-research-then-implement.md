# decompose-research-then-implement

Split 'find reliable X and integrate it' tasks into separate research and implementation phases

**Source**: Extracted from task failure (2026-03-14)
**Tags**: task-decomposition, timeout-prevention, research-tasks, agent-planning

---

## Rule: Decompose Research-Then-Implement Tasks

When a task combines **open-ended discovery** ("find reliable skills/tools") with **implementation** ("add to daily explorer"), never attempt both in a single execution.

### Why it fails
Discovery tasks have unbounded search space — evaluating scraping libraries, checking reliability, testing APIs, comparing options. Combined with integration work, total time easily exceeds agent timeout limits (~10 min).

### The correct decomposition
1. **Phase 1 — Research** (separate task): "Research options for scraping GitHub trending / HackerNews. Output: ranked list with pros/cons, code snippets."
2. **Phase 2 — Decide** (human checkpoint): Review recommendations, pick approach.
3. **Phase 3 — Implement** (separate task): "Integrate [chosen tool] into daily explorer for GitHub and HackerNews."

### Trigger signals
- Task contains both 'find/research/evaluate' AND 'add/integrate/build'
- Task references external data sources that may require API keys, rate limits, or library exploration
- Task is phrased as '...等等' ("etc.") — open scope indicator

### Application to this case
The right first move was: "List 3-5 options for scraping GitHub trending and HackerNews, with reliability notes" — not attempt discovery + integration simultaneously.
