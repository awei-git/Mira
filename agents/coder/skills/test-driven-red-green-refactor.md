---
activation_trigger: "Apply when implementing a concrete function or endpoint, fixing a reproducible bug, or any time the user asks for tests to be written first."
---

# Test-Driven Development: Red-Green-Refactor

**Tags:** coding, testing, tdd, workflow, quality

## Triggers

ACTIVATE when:
- User asks you to implement a function, method, endpoint, or class AND the behavior is concrete enough to assert ("add a /cancel endpoint that returns 404 if no active subscription")
- User reports a bug with a reproducible description ("calling withdraw with amount > balance returns 200 instead of 400")
- User says "TDD", "test first", "write a test for this", "make sure this doesn't regress"
- You're about to write logic (conditionals, loops, transforms) and no test covers the intended behavior yet

DO NOT ACTIVATE when:
- User is exploring or prototyping ("try X and see what happens", "spike this out") — spike first, test after
- The code has no logic to test: config files, re-exports, dependency wiring, type declarations
- You're writing test infrastructure itself (helpers, fixtures, factories, custom matchers)
- User explicitly says "skip tests", "we'll test later", or "just the implementation"
- The existing test suite already covers the behavior you're implementing — run it first to check

## Setup Gate

Before starting either procedure, confirm the project can run tests:
1. **Check for test infrastructure:** Look for test runner config files (`pytest.ini`, `jest.config.js`, `vite.config.ts`, `Cargo.toml` with `[dev-dependencies]`, `phpunit.xml`), test directories (`tests/`, `__tests__/`, `spec/`), or test scripts in `package.json` (`"scripts": {"test": "jest"}`) or `Makefile` (`test:` target).
2. **If no test infrastructure exists → ask:** "No test runner detected. Want me to set one up, or skip tests for now?" Do not silently create test scaffolding.
3. **If tests exist, run the suite:** Execute the test command (`npm test`, `pytest`, `cargo test`, `go test ./...`, `./vendor/bin/phpunit`). If the suite is already red, stop — fix or acknowledge existing failures before adding new tests. A red baseline makes Red-Green meaningless.

## Procedure: New Feature

1. **Name the behavior.** Write a test name that reads as a spec: `test_user_cannot_withdraw_more_than_balance`, not `test_withdraw_2`. If you can't name it → you don't understand the requirement. Stop and clarify with the user before writing anything.
2. **Red.** Write the smallest failing test — one assertion. Run it.
   - If it fails on **import/setup errors** → fix those first. A test that fails for the wrong reason proves nothing.
   - If it **passes** → either the behavior already exists (check coverage) or your assertion is wrong. Do not proceed to Green with a green test.
   - If it fails with the **expected error** (missing method, wrong return value) → proceed.
3. **Green.** Write the dumbest code that passes. Hardcode returns if needed. Do not generalize. Do not handle edge cases not yet covered by a test.
   - If you catch yourself writing an `if` branch for an untested case → stop. Write the test for that case first.
   - If getting to green takes more than a few lines of new logic → the test is too ambitious. Split it into a smaller behavior.
4. **Refactor.** Tests green → now clean up. Remove duplication, fix naming, extract helpers. Change structure only, never behavior.
   - Run tests after **every** structural change, not just at the end.
   - If a refactor breaks a test → revert the refactor immediately. Do not fix the test to match your refactored code — that's changing behavior, not structure.
5. **Repeat.** Pick the next behavior. Each Red-Green-Refactor cycle should take under 5 minutes of implementation. If it takes longer, the unit of work is too large — split it.

## Procedure: Bug Fix

1. **Reproduce.** Write a test that triggers the exact bug as reported. Run it — it must fail.
   - If you **can't** make it fail in a test → you don't yet understand the bug. Go back to investigation (read logs, add print statements, check inputs). Do not guess at a fix.
   - If the test fails but with a **different error** than the reported bug → you're testing the wrong thing. Adjust the test to match the actual report.
2. **Fix.** Change the minimum code to make the failing test pass. No "while I'm here" cleanups.
3. **Verify.** Run the full suite. The new test passes AND nothing else broke.
   - If something else broke → the fix was wrong. Revert and rethink. Do not patch the patch.

## Rules

- **Never skip Red.** Tests written after implementation confirm what you built, not what you intended. They systematically miss the failure modes you didn't think of — which are the ones that matter.
- **One assertion per test.** Multiple assertions hide which behavior broke. When a multi-assert test fails, you debug the test before you debug the code — wasted time in exactly the moment you need speed.
  ```python
  # BAD: Which assertion failed?
  def test_process_order():
      result = process_order(items=[...])
      assert result.status == 'confirmed'
      assert result.total == 100.0
      assert result.tax == 10.0

  # GOOD: One behavior per test
  def test_process_order_confirms_status():
      result = process_order(items=[...])
      assert result.status == 'confirmed'

  def test_process_order_calculates_total():
      result = process_order(items=[...])
      assert result.total == 100.0
  ```
- **Run the full suite before committing.** A single green test means nothing if it broke three others. The suite is the spec; partial green is red.
- **If it's hard to test, the design is wrong.** The test is the first client of your API. If the test needs complex setup, mocking internals, or accessing private state — fix the interface, don't write a heroic test.
  ```python
  # HARD TO TEST: Must mock internal file system calls
  class ReportGenerator:
      def generate(self):
          data = read_from_proprietary_database()  # Internal dependency
          with open('template.txt') as f:          # Internal I/O
              return fill_template(f.read(), data)

  # EASIER TO TEST: Dependencies injected, pure logic
  class ReportGenerator:
      def __init__(self, db_reader, template_loader):
          self.db_reader = db_reader
          self.template_loader = template_loader

      def generate(self):  # Now a pure transform
          data = self.db_reader.read()
          template = self.template_loader.load()
          return fill_template(template, data)
  ```

## When to Break the Cycle

TDD is a tool, not a religion. Break the cycle when:
- You're in a spike and don't yet know what the assertions should be — write tests when the spike succeeds
- The test would just restate the implementation (e.g., testing that a config dict has the right keys)
- Time pressure is real and the user has explicitly deprioritized tests for this change
