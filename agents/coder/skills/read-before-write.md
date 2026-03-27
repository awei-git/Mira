# Read Before Write

**Tags:** coding, safety, workflow

## Trigger
Use this skill when ALL of these are true:
- You are about to modify an existing file (not creating a new one)
- The function, class, or module you are changing was not written by you in this session
- The change touches code that other code depends on

Do NOT use when:
- You are writing a brand-new file from scratch with no existing callers
- You are making a trivial change to a file you just wrote minutes ago (e.g., fixing a typo)

## Start Now
Before making ANY edit to existing code, complete this checklist:

```
READ CHECKLIST:
1. [ ] Read the FULL function/class being modified (not just the lines around the change)
2. [ ] Identify all callers: grep for the function/class name across the codebase
3. [ ] Read at least 2 callers to understand how the interface is actually used
4. [ ] Check for existing tests: grep for the function name in test files
5. [ ] Read the tests to understand expected behavior and edge cases
6. [ ] Check if there are type hints, docstrings, or comments explaining intent
7. [ ] Note any invariants or assumptions the current code relies on
```

Only after ALL boxes are checked, proceed to write.

## Decision Rules

### What to read and how deep
- **Changing a function signature?** Read every caller. No exceptions. A single missed caller = a runtime crash.
- **Changing internal logic?** Read the full function + its tests. Callers matter less if the interface is stable.
- **Changing a class?** Read `__init__`, all public methods, and the most complex private method. Check for subclasses.
- **Changing a config or constant?** Grep for every reference. Config values propagate in non-obvious ways.

### The minimal-change principle
After reading, ask: "What is the smallest edit that achieves the goal?" Apply these rules:
- Change the fewest lines possible
- Preserve existing code style (indentation, naming convention, quote style)
- If the existing code uses a pattern you dislike but it works, do NOT refactor while making your change
- Add, don't replace, when possible (new branch in an if-else, new method, new parameter with default)

### Caller impact assessment
Before editing a function signature:
```
IMPACT CHECK:
- Number of callers: ___
- All callers in code I control? [yes/no]
- Can I add a default parameter instead of changing the signature? [yes/no]
- If signature must change, can I deprecate the old one first? [yes/no]
```

If callers exist outside your control (libraries, other teams, external APIs), you MUST use a backward-compatible approach: add optional parameters, create a new function and deprecate the old one, or use an adapter.

## Failure Modes

### 1. Phantom understanding
**What happens:** You glance at 5 lines around the edit point and assume you understand the function. The function has a subtle invariant 20 lines above that your change violates.
**How to catch:** Force yourself to read from the function's first line to its last line. If it's over 100 lines, read the first 30, last 30, and the section you're changing.

### 2. Orphaned callers
**What happens:** You change a function signature but miss a caller in a different directory. Code breaks at runtime, not at edit time (especially in Python/JS).
**How to catch:** Always grep for the function name. In Python, also grep for the class name if it's a method — callers might use `obj.method()` or `Class.method()`.

### 3. Test blindness
**What happens:** You make a change that passes the tests you know about, but there are integration tests in a different directory that now fail.
**How to catch:** Search for test files broadly: `grep -r "function_name" --include="*test*"` and also `--include="*spec*"`.

### 4. Style drift
**What happens:** The existing file uses single quotes, 2-space indent, and snake_case. Your edit uses double quotes, 4-space indent, and camelCase. The diff is noisy and the code looks inconsistent.
**How to catch:** Before writing, note: quote style, indent style, naming convention, import style. Match them exactly.

### 5. Editing the wrong version
**What happens:** The file has been modified by another process or branch since you last read it. Your edit overwrites their changes.
**How to catch:** Re-read the file immediately before writing if more than a few minutes have passed. In git repos, check `git status` and `git diff` before committing.
