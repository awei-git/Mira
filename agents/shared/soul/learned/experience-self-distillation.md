## When to use
After completing any non-trivial task. The goal is to never solve the same type of problem from scratch twice.

## How it works (EvolveR lifecycle)
1. **Online phase**: Execute task, record the full trajectory (what was tried, what worked, what failed)
2. **Offline self-distillation**: Review trajectory and extract 1-3 abstract principles — not specific to this task, but generalizable
3. **Curate experience base**: 
   - Deduplicate: merge principles that say the same thing differently
   - Score: track which principles actually helped when applied (effectiveness metric)
   - Prune: drop principles that never get retrieved or have low effectiveness
4. **Retrieve on new tasks**: Before starting a new task, search the experience base for relevant principles and inject them as context

## Key insight
Raw experience ("last time I did X and it worked") is fragile and specific. Distilled principles ("when facing Y-type problems, the key lever is Z") are robust and transferable. The distillation step is where learning actually happens.

## Pitfalls
- Over-distilling: extracting a "principle" from a single data point. Need at least 2-3 confirming experiences.
- Principle drift: a principle that was true in context A gets applied blindly in context B. Always check applicability.
- Experience hoarding: storing everything "just in case" defeats the purpose. Aggressive pruning is essential.

## For Mira specifically
- Journal = trajectory review. Reading notes = distillation. Worldview = curated experience base.
- The reflect cycle should explicitly score and prune worldview entries.

Source: EvolveR (arxiv 2510.16079)
