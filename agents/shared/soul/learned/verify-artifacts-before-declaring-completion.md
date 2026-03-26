# verify-artifacts-before-declaring-completion

Never declare a task complete by referencing file:// links or scheduled tasks without confirming the artifacts actually exist and are reachable

**Source**: Extracted from task failure (2026-03-24)
**Tags**: artifact-verification, completion-claims, file-paths, agent-reliability, cross-session

---

## Rule: Verify Artifacts Before Declaring Completion

When an agent reports completion and surfaces output as `file://output.md` or similar relative/local paths, it is making an unverified claim. If the file was never written, written to the wrong path, or the path is ambiguous across sessions/devices, subsequent attempts to retrieve it will silently fail or cause the agent to spin.

**What went wrong here**: The agent declared completion with `file://output.md` links and claimed a daily task was 'scheduled'. When the user followed up from iPhone asking to 'push the full report', the agent attempted to locate these artifacts and got stuck — likely because the file path was relative, never actually written, or the cron/scheduler registration was also unverified.

**The rule**:
1. After writing any output file, immediately read it back to confirm it exists and has non-trivial content.
2. After registering a cron/scheduled task, immediately list active crons to confirm registration succeeded.
3. Never surface `file://` paths to users as deliverables — use absolute paths or inline the key content directly in the response.
4. If an artifact cannot be verified, say so explicitly rather than presenting the claim as fact.

**Why this matters**: Unverified completion claims compound across sessions. The user builds plans on top of work that doesn't exist. When the gap is discovered later (especially cross-device), the agent enters a confused state trying to reconcile claimed vs actual state — producing the 'stuck' failure mode seen here.
