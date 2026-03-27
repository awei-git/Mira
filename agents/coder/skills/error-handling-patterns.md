# Error Handling Patterns

**Tags:** coding, error-handling, reliability

## Trigger
Use this skill when ALL of these are true:
- You are writing code that can fail (I/O, network, parsing, user input, external APIs)
- The failure mode is not trivially obvious (e.g., division by zero with a known denominator)
- The code will run in production or be used by other code

Do NOT use when:
- Writing throwaway scripts or one-time data exploration
- The language/framework already handles the error adequately (e.g., Rust's `?` operator in a function that already returns `Result`)

## Start Now
For every function that can fail, answer these three questions before writing error handling:

```
ERROR DESIGN:
1. What can go wrong? List every failure mode:
   - [ ] Network timeout / connection refused
   - [ ] File not found / permission denied
   - [ ] Invalid input (wrong type, out of range, malformed)
   - [ ] External service returns unexpected response
   - [ ] Resource exhaustion (memory, disk, rate limit)

2. Who should handle it?
   - [ ] This function (it can recover or provide a default)
   - [ ] The caller (it has more context to decide what to do)
   - [ ] The top-level handler (it's fatal, just log and exit cleanly)

3. What should happen on failure?
   - [ ] Retry with backoff
   - [ ] Return a default/fallback value
   - [ ] Propagate with added context
   - [ ] Log and abort the operation
```

## Decision Rules

### Catch vs. Propagate
- **Catch** when you can meaningfully recover (retry, fallback, default value)
- **Propagate** when you cannot recover — add context and let the caller decide
- **Never catch** just to log and re-raise with no added information — that's noise

### Specific vs. Broad Exceptions
- **Always catch the most specific exception available**
- Bare `except:` or `catch(e)` is almost always wrong — it swallows KeyboardInterrupt, SystemExit, and bugs
- The only place for a broad catch is the outermost handler (main loop, request handler, task runner)

### Python patterns
```python
# GOOD: Specific, with context, logged
try:
    response = requests.get(url, timeout=10)
    response.raise_for_status()
except requests.Timeout:
    logger.warning(f"Timeout fetching {url}, using cached version")
    return cached_result
except requests.HTTPError as e:
    logger.error(f"HTTP {e.response.status_code} from {url}")
    raise FetchError(f"Failed to fetch {url}") from e

# BAD: Swallows everything, hides bugs
try:
    result = do_something()
except Exception:
    pass

# BAD: Catches too broadly, logs without context
try:
    result = do_something()
except Exception as e:
    print(f"Error: {e}")
    raise
```

### JavaScript / TypeScript patterns
```javascript
// GOOD: Specific check, added context
try {
  const data = await fetch(url);
  if (!data.ok) throw new Error(`HTTP ${data.status}: ${url}`);
  return await data.json();
} catch (err) {
  if (err instanceof SyntaxError) {
    logger.error(`Invalid JSON from ${url}`, { error: err.message });
    return null;  // Explicit fallback
  }
  throw err;  // Don't swallow unknown errors
}

// BAD: Silent failure
try { doThing(); } catch (e) { /* ignore */ }
```

### The logging-on-catch rule
Every catch block that does NOT re-raise MUST log:
- **What** failed (operation name)
- **Why** it failed (error message/type)
- **What happens next** (retry, fallback, abort)

### Retry strategy
When retrying transient failures:
- Use exponential backoff: 1s, 2s, 4s (not fixed intervals)
- Set a max retry count (3 is a good default)
- Only retry on transient errors (timeouts, 429, 503) — never on 400, 401, 404
- Log each retry attempt with the attempt number

## Failure Modes

### 1. Silent swallowing
**What happens:** An empty `except: pass` hides a real bug. The program continues with corrupt state and fails mysteriously later.
**How to catch:** Grep for `except.*pass`, `catch.*{}`, and empty catch blocks. Every one is a potential time bomb.

### 2. Exception-driven control flow
**What happens:** Using try/except for expected conditions (e.g., checking if a key exists by catching KeyError instead of using `if key in dict`). This is slow and obscures intent.
**How to catch:** If the "exception" happens in normal operation (>1% of calls), it's not exceptional — use a conditional.

### 3. Lost context
**What happens:** You catch an error and raise a new one without chaining. The original traceback is lost.
**How to catch:** In Python, always use `raise NewError(...) from original_error`. In JS, set `cause`: `new Error("msg", { cause: original })`.

### 4. Overly defensive code
**What happens:** Every line is wrapped in try/except. The code is unreadable and errors are caught so early that meaningful recovery is impossible.
**How to catch:** Ask: "Can this line actually fail in a way that's different from the line above it?" If not, use one try block for the logical unit of work.

### 5. Missing cleanup
**What happens:** An error occurs between acquiring a resource and releasing it. File handles leak, connections stay open, locks are held.
**How to catch:** Use context managers (`with` in Python), `try/finally`, or RAII patterns. Never rely on the garbage collector for cleanup.
