# browser-automation-auth-prerequisite-check

Verify authenticated session exists before starting multi-step browser automation tasks; fail fast on repeated auth failures instead of exhausting step budget

**Source**: Extracted from task failure (2026-03-24)
**Tags**: browser-automation, authentication, fail-fast, task-planning

---

## Rule: Browser Automation Auth Prerequisite Check

Before launching a browser automation task that requires login:

1. **Confirm session state first.** Take a single screenshot or navigate to a known post-login page to verify an active session exists. If not logged in, surface this blocker immediately rather than attempting the full task.

2. **Fail fast on repeated auth loops.** If 3+ consecutive steps are still on a login/sign-in page, abort and report the blocker — do not burn remaining steps on the same failing navigation loop.

3. **Do not report 'waiting' without a reason.** During the stuck period (15:09–16:15), the agent responded '在等' with no explanation. If blocked on authentication, say exactly that: 'Task blocked: cannot log in to Substack. Session credentials are not available. Please provide login credentials or a valid session cookie to proceed.'

4. **Tool selection for account-specific tasks.** Tasks that read/edit content from a specific user account (e.g., 'check my notes', 'fix my posts') are publisher/account tasks, not generic web surfing. If a publisher agent is available, prefer it over a generic surfer agent for own-account operations.

5. **No silent retries.** Repeating the same goto→screenshot→goto loop without change is not progress. If the same page appears twice in a row, escalate immediately.
