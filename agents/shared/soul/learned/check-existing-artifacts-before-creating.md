# check-existing-artifacts-before-creating

Before starting any writing or creation task, verify the artifact doesn't already exist

**Source**: Extracted from task failure (2026-03-16)
**Tags**: autowrite, task-management, artifact-hygiene, session-boundary

---

## Rule: Check Existing Artifacts Before Creating

**Trigger:** Any autowrite, creation, or generation task.

**Required check:** Before beginning work, search the canonical artifacts directory (e.g. `/Users/angwei/Library/Mobile Documents/com~apple~CloudDocs/MtJoy/Mira/artifacts/writings/`) for a folder or file matching the task ID, title slug, or topic keywords.

**What happened:** At 15:06 an agent completed and published the Hayek article. At 18:45, a new agent session received the same task and started over — unaware the work was done. The user interrupted 8 times before the agent stopped.

**Failure mode:** Session boundary caused complete amnesia about prior work. The task queue or scheduler re-issued the task without a completion marker the agent could detect.

**Prevention steps:**
1. At task start, glob `artifacts/writings/*` and check for slug-match on the task title.
2. Check the episode log or task ID file (e.g. `autowrite_2026-03-16`) for a prior completion entry.
3. If artifact exists, read its metadata, confirm with user, and halt — do not restart.
4. If task system supports it, mark task complete immediately upon artifact creation, not at end of session.

**Key insight:** Duplicate-work bugs are silent until the user notices. A pre-flight existence check costs seconds; redoing hours of work (and frustrating the user into spamming the same message 8 times) costs much more.
