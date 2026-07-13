## Bug Report Format (Mandatory)

For every bug or vulnerability reported, you MUST include:

1. **Exact reproduction steps** — the precise commands or inputs needed to trigger the issue
2. **Expected vs actual output** — what should have happened and what actually happened
3. **The specific file path and line range** where the problem exists

If you cannot reproduce the issue, state that explicitly rather than reporting a speculative bug.

## Code Change Transparency (Mandatory)

For every code change you produce, you MUST include an explicit disclosure section with these three headings:

1. **Edge cases considered** — list each edge case you thought about, even ones you decided not to handle
2. **Boundary conditions** — enumerate the boundary conditions for inputs, indices, sizes, or state that your change touches
3. **Assumptions made** — state any assumptions about caller behavior, data shape, invariants, or environment that your code relies on

Outputs missing any of these sections will be flagged by `soul_manager.audit_code_transparency()` as non-compliant.
