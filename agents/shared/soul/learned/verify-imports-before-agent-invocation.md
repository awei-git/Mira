# verify-imports-before-agent-invocation

Check that all imported names actually exist in shared modules before running agent tasks

**Source**: Extracted from task failure (2026-03-25)
**Tags**: agents, imports, python, shared-modules, mira

---

## Rule: Verify Shared Module Exports Before Agent Invocation

When an agent task imports from a shared module (e.g. `sub_agent`, `utils`, `base_agent`), verify that the specific names being imported actually exist in that module before running.

**What happened:** A `socialmedia` agent task failed at runtime because it attempted `from sub_agent import run`, but `sub_agent.py` does not export a `run` function.

**How to prevent:**
1. Before invoking a new agent task, grep for the exported names in the shared module: `grep -n 'def run\|^run\|^class' /path/to/shared/sub_agent.py`
2. If the expected function doesn't exist, check whether it was renamed (e.g. `execute`, `invoke`, `start`) or lives in a different module.
3. When writing new agents that import from shared modules, read the module's actual contents — don't assume an API based on naming conventions.

**Root cause pattern:** Assumption-driven imports. The agent code assumed `sub_agent` exposes a `run` function (a common convention), but the actual module uses a different interface. This is the same failure mode as "knowing about X" ≠ "understanding X" — assuming a module's API from its name rather than reading it.

**Fix pattern:** Read `sub_agent.py` to find the correct callable, then update the import accordingly.
