# verify-imports-before-agent-dispatch

Verify that all imported names exist in their source modules before dispatching agent tasks that depend on them

**Source**: Extracted from task failure (2026-03-25)
**Tags**: python, imports, agent-tasks, shared-modules, mira

---

## Rule: Verify Imports Before Agent Dispatch

When an agent task imports from a shared module (e.g. `sub_agent`, `utils`, `helpers`), verify that the specific names being imported actually exist in that module **before** the task runs.

### What went wrong
`sub_agent.py` was imported with `from sub_agent import run`, but `run` does not exist in that module. This caused a hard failure at import time, before any task logic executed.

### How to prevent it
1. **Before writing an import**, grep or read the source module to confirm the symbol exists: `grep -n 'def run\|run =' sub_agent.py`
2. **When adding a new function to a shared module**, update all callers atomically — don't add a caller before the function exists, and don't remove a function while callers remain.
3. **When a shared module changes its public API** (rename, remove, or split a function), search for all `import` references to that module across the agents directory and update them together.
4. **For agent entrypoints specifically**: import errors are silent until dispatch time, which means the task queues, starts, and then crashes immediately — wasting a full task slot. A quick `python -c "from sub_agent import run"` smoke-check before dispatch catches this at near-zero cost.

### Applies to
- Any Python agent that imports from shared modules in `agents/shared/`
- Refactors that rename or reorganize shared utilities
- New agent tasks that reuse existing shared infrastructure
