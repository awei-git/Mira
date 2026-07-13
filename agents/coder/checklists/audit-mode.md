# Audit Mode Checklist

Use this checklist only for code review and debug tasks. In this mode, correctness must be verified, not inferred from style, naming, or plausible structure.

## Assumption Surface

- [ ] Before evaluating correctness, list the implicit assumptions the code depends on: input shape, types, ordering, filesystem state, external services, permissions, concurrency, and prior state.
- [ ] Separate assumptions that are proven by code or tests from assumptions that are only inferred from names, comments, or surrounding patterns.
- [ ] If a conclusion depends on an unverified assumption, mark the conclusion as conditional or unverified.

## Plausible-But-Wrong Scan

- [ ] Check for off-by-one boundaries, inclusive/exclusive range mistakes, empty collections, first/last item handling, and duplicate values.
- [ ] Check for inverted conditions, negated predicates, swapped branches, early returns that bypass required work, and truthiness checks that reject valid falsey values.
- [ ] Check for missing edge cases: absent files, missing keys, malformed input, partial writes, repeated calls, retries, failed subprocesses, and unexpected ordering.
- [ ] Treat idiomatic-looking code, clean formatting, familiar helper names, and green-looking control flow as insufficient evidence.

## Filesystem And State Verification

- [ ] Verify any claimed file write, deletion, move, generated artifact, or state change against the actual filesystem or authoritative state source before claiming it happened.
- [ ] If a command or tool output is used as evidence, state exactly what was checked and what remains unchecked.
- [ ] Do not report "written", "fixed", "published", "saved", or "completed" unless the artifact or state can be verified directly.

## Correctness Judgment

- [ ] For every "this looks correct" judgment, provide the concrete reason: the exact code path traced, input considered, expected result, and observed or reasoned result.
- [ ] Distinguish confirmed bugs from risks, style concerns, and unverified suspicions.
- [ ] If no issue is found, justify that conclusion with at least one traced execution path and one non-trivial edge case, not surface plausibility.
