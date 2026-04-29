---
activation_trigger: "Apply when implementing a new feature or fix, to enforce building and verifying in small steps rather than writing large unverified changesets."
---

# Incremental Development

**Tags:** coding, workflow, debugging, architecture

## Core Principle
Build and verify in small steps — write the smallest unit that can be tested, verify it works, then extend.

## Technique
Never write more than ~50 lines of new code without running it. The debugging cost of large changesets grows superlinearly: 200 lines of untested code is not 4x harder to debug than 50 lines — it's 16x harder.

The cycle:
1. **Stub** — Write the function signature and a trivial implementation (return a constant, raise NotImplementedError)
2. **Wire** — Connect it to the caller, verify the plumbing works (prints, logs, test call)
3. **Implement** — Fill in the real logic
4. **Verify** — Run it, check output matches expectations
5. **Harden** — Add edge cases, error handling only after the happy path works

When something breaks, you know the bug is in the last 50 lines you wrote — not somewhere in 500 lines of untested code.

## Application
- Start every new feature with a working skeleton (all functions stubbed, all connections wired)
- Add one real implementation at a time, testing after each
- Resist the urge to "write it all and test at the end" — the feeling of productivity is an illusion
- When fixing bugs: isolate the minimal reproduction first, then fix, then verify
