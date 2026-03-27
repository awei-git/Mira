# Safe Refactoring

**Tags:** coding, refactoring, safety

## Trigger
Use this skill when ALL of these are true:
- You need to restructure existing code (rename, extract, move, simplify)
- The code has existing callers or dependents
- The codebase has tests (or you can write them before refactoring)

Do NOT use when:
- You are writing new code that nobody depends on yet
- The code is throwaway / prototype with no tests and no callers
- You are also changing behavior — stop, separate the refactor from the behavior change

## Start Now
Before any refactor, complete this pre-flight:

```
REFACTOR PRE-FLIGHT:
1. [ ] Tests pass BEFORE the refactor (run them, don't assume)
2. [ ] I can describe the refactor in one sentence: ___
3. [ ] This refactor does NOT change behavior (if it does, split into two commits)
4. [ ] I have identified all callers/dependents of the code being changed
5. [ ] I will commit IMMEDIATELY after the refactor, before making any other changes
```

### The golden rule
**Never refactor and change behavior in the same commit.**

A refactor commit should be provably behavior-preserving. If tests pass before and after with no test changes, the refactor is safe. If you need to change tests, you are probably changing behavior.

## Decision Rules

### Choosing the right refactoring move

| Situation | Refactoring move | Risk |
|-----------|-----------------|------|
| Function does two things | **Extract Method** — pull one responsibility into a new function | Low — existing function now calls the new one |
| Bad name misleads readers | **Rename** — change name everywhere simultaneously | Medium — must find ALL references |
| Function is in the wrong file/module | **Move** — relocate and update imports | Medium — import paths change |
| Duplicated code in 2+ places | **Extract and Share** — create one function, replace all copies | Medium — the copies might differ subtly |
| Deep nesting (3+ levels) | **Early Return / Guard Clause** — invert conditions and return early | Low — logic equivalent |
| God class (does everything) | **Split Class** — extract cohesive groups of methods into new classes | High — many callers affected |
| Long parameter list (5+) | **Introduce Parameter Object** — group related params into a dataclass/struct | Medium — all callers must change |

### The Parallel Change pattern (strangler fig)
For high-risk refactors, never do a direct swap. Use three steps:

1. **Add the new version** alongside the old one (new function, new class, new module)
2. **Migrate callers** one at a time from old to new — each migration is a small, testable commit
3. **Remove the old version** only after zero callers remain

This works for: renaming public APIs, changing data formats, splitting modules, migrating database schemas.

```python
# Step 1: Add new alongside old
def get_user(user_id):          # OLD — still works
    ...

def get_user_by_id(user_id):    # NEW — better name, same behavior
    return get_user(user_id)    # delegates to old for now

# Step 2: Migrate callers one by one (separate commits)
# Step 3: Move implementation from old to new, make old delegate to new
# Step 4: Remove old after all callers migrated
```

### Extract Method checklist
1. Identify the block of code to extract
2. Check what variables from the outer scope it reads (these become parameters)
3. Check what variables it writes (these become return values)
4. If it reads 4+ variables, the extraction might not improve clarity — reconsider
5. Name the new function by its PURPOSE, not its implementation
6. Replace the original block with a call to the new function
7. Run tests

### Rename checklist
1. Identify every reference (definition, imports, calls, string references, config files, docs)
2. Use your editor/IDE's rename refactoring if available — it's more reliable than find-replace
3. If no IDE support: `grep -rn "old_name"` across the entire repo, including test files, configs, and docs
4. Make the rename in ONE commit with NO other changes
5. Run tests
6. Check for dynamic references: `getattr(obj, "old_name")`, `dict["old_name"]`, string-based lookups

## Failure Modes

### 1. Refactor + behavior change in one commit
**What happens:** You extract a method AND fix a bug in it simultaneously. The tests change. Now nobody can tell if the test change is because of the extraction or the bug fix. If the refactor introduced a bug, it's hidden by the intentional change.
**How to catch:** Ask: "Did I need to change any test assertions?" If yes, you're changing behavior. Split into two commits.

### 2. Incomplete rename
**What happens:** You rename `process_data` to `transform_data` but miss a reference in a config file, a string-based lookup, or a comment. Code breaks at runtime or becomes confusing.
**How to catch:** After renaming, grep for the OLD name. If any references remain (other than git history or changelogs), the rename is incomplete.

### 3. Extracting non-cohesive code
**What happens:** You extract a block into a function, but it needs 6 parameters and returns 3 values. The "function" is not a real abstraction — it's just a random slice of the original code.
**How to catch:** If the extracted function needs more than 3 parameters, reconsider. A good extraction has a clear name, few inputs, and one output.

### 4. Premature DRY
**What happens:** Two blocks of code look similar, so you extract a shared function. Later, the two use cases diverge. Now you have a shared function with growing if/else branches to handle each case.
**How to catch:** Wait for THREE instances of duplication before extracting. Two might be coincidence. Also: duplication is better than the wrong abstraction.

### 5. Big-bang refactor
**What happens:** You restructure 20 files in one commit. Something breaks. You can't tell which of the 20 changes caused it. Reverting means losing everything.
**How to catch:** Each refactoring move should be one commit. Extract method: commit. Rename: commit. Move file: commit. If you can't describe the commit in one line, it's too big.
