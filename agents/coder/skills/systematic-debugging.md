# Bisection Debugging

**Tags:** coding, debugging, workflow

## Trigger
Use this skill when ALL of these are true:
- You have a reliable reproduction of the bug.
- The stacktrace does NOT point directly to the root cause.
- You have already attempted at least one specific fix that failed.
- The bug exists between a known-good state (code, data, or output) and the current bad state.

Do NOT use when:
- The stacktrace line is the actual bug source (just fix it).
- You cannot reproduce the bug on demand.
- The change that introduced the bug is in code you wrote in the last 10 minutes (review your diff).

## Start Now
Copy this template and fill it in:
```
GOOD STATE:
- Commit/Version: <good_commit>
- Input/Data: <good_input>
- Output/Behavior: <good_output>

BAD STATE:
- Commit/Version: <bad_commit>
- Input/Data: <bad_input>
- Output/Behavior: <bad_output>

SEARCH SPACE: [code_path/git_history/input_data]
```

Example:
```
GOOD STATE:
- Commit/Version: abc123
- Input/Data: users_v1.csv
- Output/Behavior: Returns 200 OK with valid JSON

BAD STATE:
- Commit/Version: HEAD
- Input/Data: users_v1.csv
- Output/Behavior: Returns 500 Internal Server Error

SEARCH SPACE: git_history
```

## Method

Debugging is search. Bisection turns O(n) into O(log n). Every experiment must cut the remaining search space in half.

### Step 1: Define the search space

Pick the dimension you're bisecting:

| Search space | Signal | How to bisect |
|---|---|---|
| **Code path** | Wrong output, unclear where data goes bad | Insert assertion/log at the midpoint of the call chain. Data correct? Bug is downstream. Wrong? Upstream. Repeat. |
| **Git history** | "This used to work" | `git bisect start`, `git bisect bad HEAD`, `git bisect good <known-good-sha>`. Test each commit git selects. Mark good/bad. |
| **Input data** | Bug triggered by specific input, unclear which part | Split input in half. Which half triggers the bug? Recurse on that half. |

### Step 2: Bisect

For each iteration:
1. **Hypothesis** — state it explicitly: "The data is already wrong by the time it reaches function X"
2. **One experiment** — design it to split the remaining space in half
3. **Observe** — eliminate one half
4. **Repeat**

**Hard rule: one variable per experiment.** If you change two things and the bug disappears, you don't know which fixed it. Revert one.

#### Worked example (code path bisection)
```
Bug: API returns wrong total. Call chain: parse_request → validate → query_db → aggregate → format_response

Iteration 1: Log output of query_db. Data correct there.
  → Bug is in aggregate or format_response. (5 functions → 2)

Iteration 2: Log output of aggregate. Value already wrong.
  → Bug is in aggregate. (2 → 1)

Iteration 3: aggregate has 40 lines. Log state at line 20. Running sum correct.
  → Bug is in lines 21-40. (40 lines → 20)

Iteration 4: Log at line 30. Sum wrong.
  → Bug is in lines 21-30. Read 10 lines. Found it: off-by-one in slice boundary.

4 iterations to find a bug in a 5-function, multi-hundred-line pipeline.
```

### Step 3: Confirm the fix
- Change only the identified line. Bug gone? That's your root cause.
- Write a test that fails before the fix and passes after.
- `grep` for the same pattern elsewhere in the codebase.
