# Test-Driven Development: Red-Green-Refactor

**Tags:** coding, testing, tdd, workflow, quality

## Core Principle
Write a failing test first, make it pass with minimal code, then refactor — every line of production code is justified by a test.

## The Three-Phase Cycle

1. **Red** — Write the smallest possible failing test that captures the intended behavior *before* writing any implementation. If you can't write a test first, you don't understand the requirement well enough yet.

2. **Green** — Write the simplest code that makes the test pass. Resist the urge to generalize prematurely. Ugly code that passes is fine at this stage — clarity comes in Refactor.

3. **Refactor** — Clean up duplication, naming, and structure *only* after the test is green. Never change behavior during refactor; the passing tests are your safety net.

## Rules
- Keep each cycle under 5 minutes. If writing the test takes longer, the design is probably wrong — step back and redesign.
- Use test names as executable specifications: `test_user_cannot_withdraw_more_than_balance` beats `test_withdraw_2`.
- Run the full suite before committing. A passing suite is the only valid definition of "done."
- Never skip the Red step — tests written after the fact don't catch regressions, they just describe existing behavior.
- One assertion per test where possible. Multiple assertions obscure which behavior broke.

## Application
- Before implementing any new function, write the test that will call it.
- When fixing a bug, first write a test that reproduces the bug (it will fail), then fix it (it passes), then check nothing else broke.
- Treat the test suite as the first client of your API — if it's hard to test, the API is hard to use.

## Source
Kent Beck, *Test-Driven Development: By Example* (2002); Martin Fowler's TDD bliki (martinfowler.com)
