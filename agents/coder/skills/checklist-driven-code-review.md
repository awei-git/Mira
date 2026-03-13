# Checklist-Driven Code Review

**Tags:** coding, code-review, quality, collaboration

## Core Principle
Review code against a structured, layered checklist — architecture first, logic second, security third, style last — at a disciplined pace of 200–400 lines per hour.

## Review Layers (in order)
1. **Tests first** — Read the test suite before the implementation. Tests describe intended behavior; if they're missing or weak, that's the first comment.
2. **Design/architecture fit** — Does this change belong here? Does it respect existing boundaries? Would a different approach be simpler?
3. **Correctness and edge cases** — Null inputs, empty collections, concurrent access, error paths, off-by-one, integer overflow.
4. **Security** — Injection vectors, authentication/authorization gaps, sensitive data in logs, third-party input handling.
5. **Performance** — N+1 queries, unbounded loops, unnecessary allocations in hot paths.
6. **Readability/naming** — Are names accurate? Is the logic self-evident? Is there complexity that needs a comment?

## Rules
- Stay under 400 lines of code per hour — above that, defect detection drops sharply.
- Phrase feedback as questions or suggestions, not commands: "Could this null case crash here?" beats "Fix this."
- Classify comments by severity: bugs and security issues are **blockers**; style is **non-blocking**.
- Never review code you wrote in the same sitting — even a 20-minute break surfaces invisible issues.
- Assign reviewers who have previously touched the affected code — they give the highest-signal feedback.
- Approve when the code is good enough, not when it's perfect — the goal is shipping, not performance art.

## Application
- For each PR: run through the layers in order, leave inline comments with severity labels.
- If a review takes more than 60 minutes, ask for the PR to be split.

## Source
Dr. Michaela Greiler, *30 Proven Code Review Best Practices from Microsoft*; SmartBear Code Review Research; Graphite Engineering Blog
