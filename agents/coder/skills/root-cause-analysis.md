# Root Cause Analysis

**Tags:** coding, debugging, analysis, workflow

## Trigger
Use this skill when:
- The same bug keeps coming back after being "fixed"
- The obvious fix didn't work and you don't know why
- You found WHAT is broken but not WHY

Do NOT use when:
- The stacktrace points to the exact line and the fix is obvious
- You haven't tried the obvious fix yet (try simple first)

## Start Now

Fill in this template:
```
SYMPTOM: [What the user sees / what fails]
IMMEDIATE CAUSE: [The line/function that produces the wrong result]
WHY (1): [Why does that line produce wrong result?]
WHY (2): [Why does the answer to WHY (1) happen?]
WHY (3): [Keep going until you reach something you can fix permanently]
ROOT CAUSE: [The deepest WHY that, if fixed, prevents recurrence]
```

## The Five Whys Method

Ask "why" at least 3 times, at most 5. Stop when you reach a systemic cause.

### Example
```
SYMPTOM: Heartbeat shows "offline" in app
IMMEDIATE CAUSE: heartbeat.json timestamp is 47 minutes old
WHY (1): bridge.heartbeat() is failing every cycle
WHY (2): heartbeat() called with unexpected keyword argument 'agent_status'
WHY (3): core.py was updated to pass agent_status but mira_bridge.py was not updated
ROOT CAUSE: No interface contract between core.py and mira_bridge.py — function signature
changed in one file but not the other, and no test catches the mismatch.

FIX: Add agent_status parameter to bridge.heartbeat()
PREVENT: Add integration test that calls heartbeat() with current core.py's arguments
```

## Decision Rules

### When the immediate fix is NOT enough
The immediate fix handles the symptom. Ask:
- **Will this exact bug recur?** If yes, you haven't found the root cause.
- **Could a similar bug happen elsewhere?** Grep for the same pattern.
- **Is there a missing test?** The root cause is often "no test for this path."

### Classifying root causes
| Root cause type | Example | Systemic fix |
|---|---|---|
| **Interface mismatch** | Caller passes args callee doesn't accept | Add type hints + integration test |
| **Missing validation** | Bad data enters system unchecked | Validate at boundary, fail fast |
| **Race condition** | Two writers, no lock | Add locking/atomicity |
| **Implicit assumption** | Code assumes file exists, doesn't check | Make assumption explicit (assert or check) |
| **Stale state** | Cache not invalidated | Add invalidation trigger or TTL |
| **Missing error handling** | Exception swallowed, caller unaware | Propagate or handle with logging |

### When to stop digging
- You've reached something you can change (code, config, process)
- Going deeper would mean questioning language/OS design (too deep)
- The root cause, if fixed, would prevent this entire category of bug

## Failure Modes

### 1. Stopping too early
You find "the line that crashes" and fix it. But the real cause is 3 layers deeper — bad data entered the system 5 functions earlier.
**Fix:** Always ask "why does THIS wrong value exist here?" at least once more.

### 2. Going too deep
"The root cause is that Python allows mutable default arguments." This is true but not actionable.
**Fix:** Stop at the deepest cause you can actually change in this codebase.

### 3. Fixing symptoms in multiple places
You find the same bug in 5 files and fix all 5. But next week a 6th file gets the same bug.
**Fix:** Find the shared cause. Maybe a utility function should enforce the correct pattern so individual files can't get it wrong.
