When an autonomous agent repeats prohibited behavior despite instructions, apply this three-layer audit:

1. **Instruction persistence check**
   - Identify all places where "don't do X" could live: conversation history, config files, CLAUDE.md, env vars, flags in code.
   - For each execution path in the pipeline (especially scheduled/autonomous ones), determine which of those sources it actually reads at runtime.
   - If the pipeline reads configs but not conversation history, verbal instructions are invisible to it by design.

2. **Global disable switch audit**
   - Check whether a single flag can halt all output paths, or whether each path has its own check.
   - If there is no unified kill switch, patching one path leaves others open. This is the "whack-a-mole" failure mode.

3. **Deduplication semantics check**
   - If the pipeline produces content, check how it determines "already done this."
   - Exact-match (title, ID) is easily bypassed by surface variation. Ask: is deduplication semantic or syntactic?

Root cause framing to use in diagnosis:
- "Verbal instruction → conversation record → not read by pipeline" = persistence gap
- "Patched path A, path B still runs" = no unified enforcement point
- "Catalog matched by title, not meaning" = syntactic deduplication

Fix pattern: for any prohibition to be reliable, it must be (a) written to a file the pipeline reads, (b) checked by a single shared function all paths call, and (c) enforced before output, not just flagged after.