# permission-revocation-must-propagate-to-pipelines

When a user revokes permission for an external action, immediately audit and disable ALL automated pipelines that could trigger it — not just the current session's behavior.

**Source**: Extracted from task failure (2026-03-16)
**Tags**: authorization, publishing, automation, pipeline, external-actions

---

## Rule: Permission Revocation Must Propagate to All Automation

**Trigger**: User says any variant of "don't do X anymore" where X is an external, visible, or irreversible action (publishing, sending, posting, deploying).

**What went wrong here**: The user had previously revoked Substack publishing permission. A background task or pipeline retained the old authorization and fired anyway — multiple times (duplicate posts), suggesting the automation was never audited after the revocation.

**The rule**:
1. When permission is revoked for any external-facing action, immediately ask: *"Is there any scheduled task, pipeline, or background process that could still trigger this?"*
2. If yes — find it, disable it, confirm to the user it's off before the conversation ends.
3. Do not assume verbal acknowledgment of a revocation is sufficient. Revocation is only complete when the automation is provably stopped.
4. For publishing specifically: check cron jobs, queued tasks, workflow triggers, and any "auto-publish on merge/approval" logic.

**The asymmetry that makes this critical**: A user saying "don't publish" expects zero publications. One accidental publish is a 100% failure rate. Duplicate accidental publishing makes it unambiguously a systemic automation failure, not a one-off.

**Confirmation pattern after revocation**:
> "You've said not to publish to Substack. I've [specific action taken to stop it]. Here's what I disabled: [list]. Confirm this covers everything?"
