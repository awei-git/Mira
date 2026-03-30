---
name: Diagnostic Error Handling
description: Task failures must emit enough context to diagnose root cause without re-running
tags: [agents, error-handling, logging, observability, reliability]
---

## Core Principle

When a task fails, the error record must contain enough information to diagnose the root cause without re-running the task. A message like "无法生成回复" is a symptom, not a cause — it could mean content policy block, empty input, context overflow, rate limiting, or network fault. These require entirely different responses.

## What Every Error Record Must Capture

1. **Specific failure point** — which pipeline stage failed (input validation? generation? post-processing?)
2. **Input state** — was there content to process? what was its shape/length?
3. **Error class** — policy, resource, logic, or transient
4. **Whether retry is safe** or contraindicated
5. **System state** — relevant config, environment, upstream dependencies

## Rules

- If error context is identical to the task title, the logging pipeline is broken — flag this as a separate signal
- Distinguish transient failures (network, timeout) from structural ones (logic, missing data)
- For generation tasks: log input hash or length at failure time — silent failures often trace to missing/empty input
- If a failure cannot be diagnosed from its record alone, the record itself is a second failure

## Test

Can you read this error record six months later and know what to fix? If not, the error instrumentation is broken.
