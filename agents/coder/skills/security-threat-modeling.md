# Security Threat Modeling at Design/Review Time

**Tags:** coding, security, threat-modeling, architecture, review

## When to Activate
Trigger this skill when a change introduces or modifies:
- A new API endpoint or external-facing surface
- Authentication or authorization logic
- A data store, cache, or queue (new or changed schema)
- A trust boundary crossing (service-to-service calls, user input processing, third-party API integration)
- Secrets handling, token generation, or session management
- Infrastructure config (network rules, IAM policies, container permissions)

Do NOT activate for: pure refactors within a single trust zone, UI-only changes with no new data flow, test-only changes.

## Quick Start (5-Minute Check)
Before deep analysis, run these checks on the code diff:
1. **Auth Check:** Find every new/changed endpoint. Verify each has an authentication check visible in the diff.
2. **Owner Check:** For any endpoint fetching/updating by ID (e.g., `/api/resource/{id}`), verify the code compares `resource.owner_id == request.user.id` (or equivalent).
3. **Error Leak Check:** Open every `catch` block or error handler in the diff. Verify error responses to users are generic (e.g., "Something went wrong").
4. **Expensive Operation Check:** Find any loop, file processing, or external call triggered by user input. Verify rate limiting exists or is already applied.
5. **Secret Check:** Search diff for `password`, `key`, `secret`, `token`. Verify none are logged, returned in errors, or stored in plaintext.

If any check fails, it's likely a **Block** finding. Proceed to full procedure.

## Procedure

### Step 1: Identify the trust boundaries in the change
List every point where data crosses a privilege or trust level. Examples:
- Browser → API server (untrusted → trusted)
- API server → database (application → storage)
- Service A → Service B (different auth domains)
- User upload → file processing pipeline (untrusted content → system execution)

Third-party APIs count as trust boundaries — the external service can be compromised, return malicious data, or go down. Treat outbound calls with the same rigor as inbound user input.

If the change has zero boundary crossings, stop — no threat model needed.

### Threat Modeling Cheat Sheet
For common patterns, focus on these high-probability threats:

| Pattern | Likely Threats | Questions to Ask | Verify First |
|---------|----------------|------------------|--------------|
| **API Endpoint** (REST/GraphQL) | Spoofing, Elevation, DoS | - Auth check present?<br>- Ownership verified?<br>- Rate limiting applied? | Check auth middleware attachment & IDOR in path params |
| **File Upload** | Tampering, Info Disclosure, Elevation | - File type validated?<br>- Scan for malware?<br>- Metadata stripped? | Check file extension vs. content-type mismatch |
| **Background Job** (queue/worker) | Tampering, Repudiation, Info Disclosure | - Job payload signed?<br>- Job completion logged?<br>- Errors contain secrets? | Check job payload validation before processing |
| **Service-to-Service Call** | Spoofing, Tampering, Info Disclosure | - Mutual TLS or tokens?<br>- Request signing?<br>- Error details exposed? | Check authentication method & error handling |
| **Database Query** (new/changed) | Info Disclosure, Elevation | - SQL injection protected?<br>- Row-level access control?<br>- Sensitive columns masked? | Check parameterized queries & WHERE clause logic |
| **Webhook/Callback** | Spoofing, Tampering, Repudiation | - Signature verified?<br>- Idempotent handling?<br>- Source IP restricted? | Check signature validation before business logic |

### Step 2: For each boundary, ask the six STRIDE questions
Don't enumerate abstractly. Ask against the specific data flow:

| Threat | Concrete question for this boundary |
|--------|--------------------------------------|
| Spoofing | What proves the caller is who they claim? Is that proof forgeable? |
| Tampering | Can the payload be modified between sender and receiver? Is integrity verified? |
| Repudiation | If this action causes harm, can we prove who did it and when? |
| Info Disclosure | What does the error path reveal? What do logs capture? What leaks in timing? |
| Denial of Service | What's the cost ratio? (Can a $0.01 request trigger $10 of compute?) |
| Elevation of Privilege | If the caller controls field X, can they reach resources beyond their scope? |

**While asking these, check error paths explicitly.** Most info disclosure happens in catch blocks, logging, and error serialization — not the happy path. Open every error handler in the diff and ask: what does this reveal?

### Step 3: Translate each real threat into a test
A threat without a test is a wish. Map like this:

**Example: API endpoint `/api/documents/{id}` returns documents by ID**

Boundary: Browser → API server

| Threat found | Mitigation | Test |
|---|---|---|
| Spoofing: no auth check on endpoint | Add auth middleware | `test_unauthenticated_request_returns_401()` |
| Elevation: user A can fetch user B's doc via IDOR | Check `doc.owner_id == request.user.id` | `test_user_cannot_access_other_users_document()` |
| Info Disclosure: stack trace in 500 response | Generic error in prod, details to structured log only | `test_internal_error_returns_generic_message()` |
| DoS: no rate limit on endpoint | Rate limiter at 100 req/min/user | `test_rate_limit_returns_429_after_threshold()` |

```python
# Concrete test pattern — IDOR check
def test_user_cannot_access_other_users_document(client, user_a, user_b):
    doc = create_document(owner=user_b)
    resp = client.get(f"/api/documents/{doc.id}", headers=auth_header(user_a))
    assert resp.status_code == 404  # 404 not 403 — don't confirm existence
```

### Step 4: Common Pitfalls to Avoid
These subtle mistakes often survive initial threat modeling:

1. **Incomplete Authorization:** Checking `user.role == 'admin'` but forgetting to verify the user is still active/not suspended.
   *Fix:* `user.role == 'admin' AND user.status == 'active'`

2. **Timing Side Channels:** Using string comparison for tokens/secrets (`.equals()` in Java, `==` in Python) that leaks match length via timing.
   *Fix:* Use constant-time comparison functions (`hmac.compare_digest()` in Python, `MessageDigest.isEqual()` in Java).

3. **Partial Input Validation:** Validating JSON schema but allowing extra fields that bypass business logic.
   *Fix:* Reject unknown fields or explicitly strip them before processing.

4. **Race Condition in Ownership Check:**
   ```python
   # WRONG: Check-then-act pattern
   if document.owner_id == user.id:  # Check
       document.delete()             # Act - window for race
   ```
   *Fix:* Single atomic operation: `DELETE FROM documents WHERE id = ? AND owner_id = ?`

5. **Logging Before Redaction:** Logging the full request/response for debugging, including tokens in headers or PII in body.
   *Fix:* Use structured logging with explicit allow-lists of safe fields, redact everything else.

### Step 5: Triage — block, track, or accept
Not all findings are equal. Classify each threat on two axes:

|  | **High impact** (data breach, privilege escalation, auth bypass) | **Low impact** (info leak of non-sensitive data, minor availability risk) |
|---|---|---|
| **Easy to exploit** (unauthenticated, no special knowledge needed) | **Block PR** — fix before merge | **Track** — file issue, fix within sprint |
| **Hard to exploit** (requires insider access, race condition, chained with another bug) | **Track** — file issue, fix within sprint | **Accept** — document in threat table, revisit if attack surface changes |

Decision rules:
- Any finding in the **Block** cell must have a passing test before the PR merges.
- Any finding in a **Track** cell gets a filed issue linked in the PR description. The issue must include the threat, the proposed mitigation, and the test name.
- **Accept** findings are recorded in the threat table for audit trail. If three or more Accept findings accumulate on the same boundary across PRs, escalate to Track.
- If any unauthenticated or low-privilege action triggers expensive work (DB full-table scans, file processing, external API fan-out) with no rate limit, that is always **Block** regardless of perceived impact — cost-asymmetry DoS is reliably exploitable.

## Output Format
When reporting results, produce:
1. **Boundary list** — one line per trust boundary in the change
2. **Threat table** — threats found, mitigations proposed, test names, and disposition (Block / Track / Accept)
3. **No-issue boundaries** — explicitly note boundaries you checked and found no issues (proves coverage, not omission)
4. **Blocking items** — if any, list the specific tests that must pass before merge
