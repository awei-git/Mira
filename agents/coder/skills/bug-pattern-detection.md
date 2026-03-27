# Bug Pattern Detection

**Tags:** coding, debugging, review, patterns

## Trigger
Use this skill when:
- Reviewing code for bugs (before or after they manifest)
- Investigating why something "works sometimes but not always"
- Doing a pre-merge review of a PR or changeset

## Scan Checklist

Read code looking for these patterns. Each is a likely bug.

### Concurrency & State
- [ ] **Shared mutable state without lock** — two threads/processes writing same file/variable
- [ ] **Read-modify-write without atomicity** — `x = load(); x += 1; save(x)` loses updates under concurrency
- [ ] **Time-of-check-to-time-of-use (TOCTOU)** — `if file.exists(): file.read()` can race
- [ ] **Stale cache** — cached value not invalidated when source changes

### Error Handling
- [ ] **Bare except / except Exception: pass** — swallows real errors, hides bugs
- [ ] **Error logged but not raised** — caller thinks operation succeeded
- [ ] **Partial failure not rolled back** — wrote file A but file B write failed, now inconsistent
- [ ] **Retry without backoff** — hammers a failing service, makes it worse

### Data & Types
- [ ] **Off-by-one** — `range(len(x))` vs `range(len(x)-1)`, fence-post errors
- [ ] **None/null not checked** — function can return None but caller assumes value
- [ ] **String vs bytes confusion** — encoding errors lurking in string operations
- [ ] **Integer overflow / float precision** — money calculations with float, large ID as 32-bit int
- [ ] **Mutable default argument** — `def f(items=[])` shares list across calls in Python

### Resource Leaks
- [ ] **File/connection opened but not closed** — missing `with` statement or `finally` block
- [ ] **Subprocess spawned but not waited on** — zombie processes
- [ ] **Temp files created but not cleaned up** — disk fills up over weeks

### Logic
- [ ] **Negation error** — `not x or y` when you meant `not (x or y)`
- [ ] **Short-circuit bypass** — `if a and b()` never calls b() when a is False — is that intended?
- [ ] **Fallthrough without break** — in switch/match statements
- [ ] **Wrong comparison** — `is` vs `==`, `=` vs `==` in conditions

### Security
- [ ] **User input in SQL/shell/eval** — injection vector
- [ ] **Secrets in logs** — API keys, tokens, passwords logged at INFO level
- [ ] **Path traversal** — user-controlled filename used in `open()` without sanitization
- [ ] **CORS/auth bypass** — endpoint accessible without authentication check

## How to Apply

When reviewing code:
1. Read the diff once for understanding
2. Read it again scanning ONLY for patterns above (one category at a time)
3. For each hit: file:line + pattern name + why it's dangerous
4. Severity: **critical** (data loss, security), **high** (incorrect behavior), **medium** (resource leak), **low** (code smell)

## Common False Positives
- Bare `except` in crash handlers / last-resort cleanup — this is intentional
- `is None` checks that look like `is` misuse — `is None` is correct Python
- Mutable default when the function immediately copies it — not a bug
