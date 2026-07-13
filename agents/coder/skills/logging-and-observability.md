---
activation_trigger: "Apply when writing production or background-process code that has invisible failure modes requiring post-hoc debugging, monitoring, or auditing."
---

# Logging and Observability

**Tags:** coding, logging, observability, debugging

## Trigger
Use this skill when ALL of these are true:
- You are writing code that will run in production or as a background process
- The code has failure modes that won't be immediately visible (no human watching the terminal)
- You need to debug, monitor, or audit the system after the fact

Do NOT use when:
- Writing a pure function with no side effects that is covered by unit tests
- Adding temporary debug prints during development (remove these before committing)

## Start Now
For every module or service, answer these questions to design your logging:

```
LOGGING PLAN:
1. What are the system boundaries? (API calls, file I/O, DB queries, external services)
   → Log at every boundary crossing

2. What decisions does the code make? (routing, retries, fallbacks, cache hits/misses)
   → Log every decision with the inputs that drove it

3. What errors can occur? (network failures, invalid data, timeouts)
   → Log every error with full context

4. What should NEVER be logged? (passwords, tokens, PII, credit cards)
   → List sensitive fields and ensure they're excluded
```

## Decision Rules

### What to log

| Category | Log Level | Example |
|----------|-----------|---------|
| System startup/shutdown | INFO | `"Server started on port 8080"` |
| Request received / response sent | INFO | `"GET /api/users → 200 (45ms)"` |
| External service call | INFO | `"Calling OpenAI API: model=gpt-4, tokens=500"` |
| Decision point | INFO | `"Cache miss for user_123, fetching from DB"` |
| Retry attempt | WARNING | `"Retry 2/3 for fetch_feed: timeout after 10s"` |
| Recoverable error | WARNING | `"Invalid date format in row 42, skipping"` |
| Unrecoverable error | ERROR | `"Database connection failed: Connection refused"` |
| Should-never-happen condition | ERROR | `"Invariant violated: balance negative after deposit"` |
| Performance trace (hot paths) | DEBUG | `"parse_document took 230ms for 15KB input"` |
| Data structure contents | DEBUG | `"Feed items after filter: count=12, sources=[arxiv, reddit]"` |

### What NOT to log
- **Secrets:** API keys, tokens, passwords, connection strings with credentials
- **Personal data:** Full names, emails, addresses, phone numbers (unless required and consented)
- **Large payloads:** Full request/response bodies — log a summary (size, type, key fields)
- **High-frequency noise:** Don't log inside tight loops. Log the aggregate instead.

```python
# BAD: Logs the API key
logger.info(f"Calling API with key={api_key}")

# GOOD: Logs the operation without secrets
logger.info(f"Calling API: endpoint={endpoint}, model={model}")

# BAD: Logs every item in a 10,000-item loop
for item in items:
    logger.info(f"Processing {item}")

# GOOD: Log the batch
logger.info(f"Processing {len(items)} items from {source}")
```

### Log level guide
- **DEBUG:** Information useful only when actively debugging. Turned off in production by default. Content: internal state, timing, data shapes.
- **INFO:** Normal operations that confirm the system is working. Someone monitoring should see these and feel confident. Content: requests, completions, decisions.
- **WARNING:** Something unexpected happened but the system recovered. Action may be needed soon. Content: retries, fallbacks, degraded service.
- **ERROR:** Something failed and the operation could not complete. Action needed. Content: exceptions, failed operations, data corruption.
- **CRITICAL/FATAL:** The system itself is compromised. Immediate action required. Content: cannot connect to database, disk full, out of memory. Use sparingly.

### Structured logging
Use structured (key-value) logging over free-form strings. It makes searching and filtering possible.

```python
# BAD: Free-form string — hard to search, parse, filter
logger.info(f"User {user_id} uploaded {file_name} ({file_size} bytes)")

# GOOD: Structured fields — searchable, filterable
logger.info("File uploaded", extra={
    "user_id": user_id,
    "file_name": file_name,
    "file_size": file_size,
})
```

```javascript
// GOOD: Structured logging in JS
logger.info({ userId, fileName, fileSize }, "File uploaded");
```

### Context propagation
Every log message should answer: WHO, WHAT, WHEN, and RESULT.
- **WHO:** Request ID, user ID, session ID — something to correlate related logs
- **WHAT:** The operation being performed
- **WHEN:** Automatic via logging framework timestamps
- **RESULT:** Success/failure, duration, key output metrics

## Failure Modes

### 1. Logging secrets
**What happens:** An API key or password appears in log files. Anyone with log access now has credentials. If logs are shipped to a third-party service, the secret is now external.
**How to catch:** Grep your log statements for variable names containing `key`, `secret`, `token`, `password`, `auth`. Use a secrets scanner on log output.

### 2. Log-and-forget
**What happens:** You log an error but take no action. The error message scrolls by in a sea of INFO messages. The problem persists for weeks.
**How to catch:** Every ERROR-level log should have a corresponding alert, retry, or escalation path. If nobody will see it, the log is useless.

### 3. Excessive logging in hot paths
**What happens:** You log inside a loop that runs 100,000 times. Log volume explodes, disk fills up, performance degrades, and the useful logs are buried.
**How to catch:** Never log inside loops without rate limiting. Log the batch summary (count, duration, errors) instead.

### 4. Missing context
**What happens:** The log says `"Error processing request"` but doesn't say which request, which user, or what the error was. Useless for debugging.
**How to catch:** Every log message should be self-contained enough to answer "what happened?" without reading the surrounding log lines. Include IDs, operation names, and error details.

### 5. Wrong log level
**What happens:** Normal operations logged as WARNING/ERROR — creates alert fatigue. Real errors logged as INFO — never noticed.
**How to catch:** Review: if this message fires every minute during normal operation, it's INFO or DEBUG, not WARNING. If this message means a user's request failed, it's ERROR, not INFO.
