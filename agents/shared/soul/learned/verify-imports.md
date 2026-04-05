---
name: Verify Imports
description: Verify that all imported names exist in shared modules before running agent tasks
tags: [agents, python, imports, shared-modules, reliability]
---

## Core Principle

When an agent task imports from a shared module, verify that the specific names being imported actually exist in that module before the task runs. Import errors are silent until dispatch time — the task queues, starts, and crashes immediately, wasting a full slot.

## Rules

1. **Read before importing.** Before writing `from module import X`, grep or read the source module to confirm `X` exists: `grep -n 'def X\|^X\|^class X' module.py`

2. **Don't assume APIs from names.** The agent assumed `sub_agent` exposes `run` (a common convention), but it uses a different interface. Read the module's actual contents.

3. **Update callers atomically.** When adding a function to a shared module, update all callers together. When removing or renaming, search for all `import` references across the agents directory first.

4. **Smoke-test before dispatch.** A quick `python -c "from module import X"` catches import errors at near-zero cost before committing a task slot.

## Applies To

- Any Python agent importing from `agents/shared/`
- Refactors that rename or reorganize shared utilities
- New agent tasks reusing existing shared infrastructure
