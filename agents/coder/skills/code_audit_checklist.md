---
activation_trigger: "Apply when the coder agent is reviewing AI-generated code, a diff, a patch, or any task classified as a code review."
---

# Code Audit Mindset

Use this skill when reviewing code, especially AI-generated code. Creation and audit are different modes: do not judge code by whether it resembles a plausible solution. Judge it by whether each behavior is actually correct against the stated specification.

## Mandatory Verification Protocol

1. Restate the specification in concrete terms before assessing the code.
2. Trace every assumption the code relies on, including input shape, state, ordering, types, external contracts, and error behavior.
3. Walk the relevant code line by line. For each meaningful branch, loop, condition, return, mutation, or side effect, state what it does and whether that behavior is justified.
4. Identify edge cases explicitly, including empty input, missing fields, malformed input, boundary values, duplicate values, unexpected ordering, partial failure, and repeated calls.
5. Test the reasoning with minimal inputs and erroneous inputs. If you cannot run tests, simulate the execution with concrete values and say what remains unverified.
6. Compare the observed or simulated output to the specification. Treat a mismatch as a finding even if the code looks idiomatic.
7. Flag every part that is merely plausible but unverified. Do not let naming, formatting, familiar patterns, or surrounding correct code stand in for proof.
8. Confirm that the final assessment is based on actual correctness, not surface resemblance to code you would have generated.

## Required Output

Before the final verdict, include a section titled `## Audit Pass`.

The audit pass must contain line-by-line checks with file and line references where available. Each check must state:

- the code being checked
- the assumption or behavior under test
- the edge case or concrete input considered
- the correctness result: `verified`, `bug`, or `unverified`

After `## Audit Pass`, provide the final verdict and clearly separate confirmed issues from risks that remain unverified.
