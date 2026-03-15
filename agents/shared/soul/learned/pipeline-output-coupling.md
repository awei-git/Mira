# pipeline-output-coupling

When one pipeline stage produces output that a downstream stage consumes, the output must be written at the producer — never rely on a separate batch export.

**Source**: Podcast pipeline failure (2026-03-15) — `publish_to_substack()` didn't copy to `_published/`, so `should_podcast()` couldn't find new articles.
**Tags**: pipeline, architecture, agent-reliability, data-coupling

---

## Rule: Couple Output at the Producer

When pipeline A's output is pipeline B's input, A must write to B's expected location as part of its own completion — not as a separate batch job that can fall out of sync.

### Why This Matters

A batch export creates a hidden dependency: it works once, then silently breaks when new items are added through the normal flow. The failure is invisible because the producer succeeds (article published) and the consumer succeeds (no articles to process), but the connection between them is broken.

### Concrete Example

- `publish_to_substack()` publishes an article but didn't copy to `_published/`
- `should_podcast()` scans `_published/` for articles missing episodes
- New articles after the initial batch export were invisible to podcast generation
- No error was raised — the system looked healthy but was doing nothing

### Pattern to Follow

1. When adding a new pipeline stage that reads from a directory, check: does every writer to that directory write at the point of creation?
2. If a batch export exists, it should be a recovery mechanism, not the primary path
3. Log when a downstream consumer finds zero inputs — silence on "nothing to do" hides broken couplings
