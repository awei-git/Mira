---
name: Task Decomposition
description: Break complex tasks into bounded sub-tasks before execution to prevent timeouts and silent failures
tags: [agents, task-management, planning, decomposition, timeout-prevention]
---

## Core Principle

When given a task that could take more than 2-3 minutes, stop and decompose it into sub-tasks before writing any code or running any commands. Never attempt research + implementation in a single execution.

## When to Decompose

- Task touches multiple files or systems
- Task mixes discovery ("find/research/evaluate") with implementation ("add/integrate/build")
- Task has sequential dependencies (A must complete before B)
- Any single step might exceed agent timeout (~10 min)
- Scope is ambiguous or open-ended ("refactor X", "加上Y功能", "...等等")

## How to Decompose

1. **List all distinct actions** before starting any of them
2. **Split by timeout profile**: research tasks (2-5 min) vs code tasks (5-10+ min) must be separate
3. **Separate research from implementation**: Phase 1 = research + recommend, Phase 2 = decide, Phase 3 = implement
4. **Split at read boundaries**: if >3 file reads needed before writing, make "read and summarize" a separate step
5. **Estimate each sub-task**: if any feels like >3 min, split further
6. **Execute one at a time**, marking complete before moving on

## Anti-Patterns

- Starting to write code before the full plan is clear
- Bundling research + implementation + verification into one action
- Treating "I know what to do" as equivalent to "this will fit in one step"
- Saying "加上Notes功能" as one task when it requires reading, understanding, writing, and integrating

## Recovery

If a task times out, do not retry the same monolithic approach. Re-read the request, write out every discrete step, then begin execution one step at a time.
