---
activation_trigger: "Apply when an AI agent has just generated or is about to generate non-trivial code that will be committed, deployed, or used by others."
---

# AI Code Generation Quality

**Tags:** coding, ai, quality, self-check

## Trigger
Use this skill when ALL of these are true:
- You (an AI agent) have just generated or are about to generate code
- The code will be committed, deployed, or used by others
- The code is more than a trivial one-liner

Do NOT use when:
- You are generating throwaway exploratory code that will be immediately discarded
- You are writing pseudocode or documentation examples

## Start Now
Before submitting ANY generated code, run this self-check:

```
AI CODE SELF-CHECK:
1. [ ] Every import/require references a real package that actually exists
2. [ ] Every API call uses a real method with correct argument names and types
3. [ ] Every function I call on a library object is a real method of that object
4. [ ] I have NOT invented a convenience method that doesn't exist
5. [ ] The code handles the error/empty/null case, not just the happy path
6. [ ] I tested my logic mentally with: (a) normal input, (b) empty input, (c) edge case
7. [ ] I have not over-engineered: no unnecessary abstractions, classes, or design patterns
8. [ ] The code matches the style and patterns of the existing codebase
9. [ ] I have not mixed up similar APIs (e.g., fs.readFile vs fs.readFileSync)
10. [ ] No placeholder comments like "implement this" or "TODO: add error handling"
```

## Decision Rules

### The hallucinated API problem
This is the #1 failure mode of AI-generated code. The code looks correct, reads well, and uses an API that does not exist.

**Common hallucination patterns:**
- Inventing a method that "should" exist: `response.json_safe()`, `path.ensure_exists()`, `list.unique()`
- Using a method from the wrong version: `asyncio.run()` exists in Python 3.7+ but not 3.6
- Confusing similar libraries: `requests.get()` vs `httpx.get()` — similar but different return types
- Inventing keyword arguments: `open(file, encoding="utf-8", errors="skip")` — `errors="skip"` is not valid, it's `errors="ignore"`
- Using a method from language A in language B: `.push()` (JS) vs `.append()` (Python)

**How to prevent it:**
- If you're unsure whether an API exists, say so explicitly. "I believe this method exists but cannot verify" is better than silently using a fake API.
- For critical code, include a comment: `# Verify: does library X have method Y?`
- Prefer well-known, heavily-documented APIs over obscure ones

### The outdated pattern problem
AI training data includes old code. You may generate patterns that were correct 3 years ago but are now deprecated or anti-pattern.

**Common outdated patterns to avoid:**
- Python: `os.path.join()` instead of `pathlib.Path` (pathlib is preferred in modern Python)
- Python: `%s` string formatting instead of f-strings
- JS: `var` instead of `const`/`let`
- JS: callbacks instead of async/await
- React: class components instead of function components with hooks
- Any language: rolling your own JWT parsing instead of using a vetted library

**How to prevent it:** Look at the existing codebase's patterns. If they use pathlib, you use pathlib. Match the codebase, not your training data.

### The over-engineering problem
AI tends to generate architecturally "impressive" code: abstract base classes, factory patterns, strategy patterns, dependency injection — when a simple function would suffice.

**The test:** Remove each abstraction layer mentally. Does the code still work with fewer layers? If yes, remove the layer.

**Rules of thumb:**
- Don't create a class if a function will do
- Don't create an abstract base class if there's only one implementation
- Don't create a factory if objects are constructed in only one place
- Don't add a config system if there are fewer than 5 configurable values
- Don't create an enum for fewer than 3 values

### The missing edge case problem
AI-generated code tends to handle the happy path perfectly and miss:
- Empty lists/strings/dicts passed as input
- None/null/undefined where a value is expected
- Unicode and special characters in string processing
- Very large inputs (memory, timeout)
- Concurrent access (race conditions)
- File/network operations that fail partway through

**For every function, ask:** What happens if the input is empty? What if it's None? What if it's enormous?

### The copy-paste drift problem
When generating similar code for multiple cases (e.g., handlers for different endpoints), AI often copy-pastes the first version and fails to update all the details. Variable names from case 1 leak into case 2.

**How to catch:** After generating repetitive code, read EACH instance independently. Check that variable names, string literals, and comments are correct for THAT specific instance.

## Failure Modes

### 1. Confident incorrectness
**What happens:** The AI generates code with no hedging, no comments, and no uncertainty markers. The code looks authoritative. It uses an API that doesn't exist. The developer trusts it because it "looks right."
**How to catch:** The more confident the generated code looks, the more carefully you should verify API calls and method signatures. Confidence is not evidence.

### 2. Works-on-my-training-data
**What happens:** The code uses a library version, OS feature, or language feature that exists in the AI's training data but not in the target environment.
**How to catch:** Check the project's language version, dependency versions, and target platform. Don't assume the latest version of anything.

### 3. Incomplete implementation with TODO markers
**What happens:** The AI generates the structure but leaves critical sections as `# TODO: implement` or `// ... handle edge cases`. The developer doesn't notice the TODOs and ships incomplete code.
**How to catch:** Grep the generated code for `TODO`, `FIXME`, `HACK`, `XXX`, `implement`, `placeholder`. Every one must be resolved before committing.

### 4. Test that tests nothing
**What happens:** AI generates a test that always passes — it tests the mock, not the code. Or the assertion is so weak (`assert result is not None`) that it can't catch real bugs.
**How to catch:** For each test, ask: "What bug would cause this test to fail?" If you can't name a specific bug, the test is useless. Good tests assert specific values, specific types, and specific behaviors.

### 5. Style mismatch
**What happens:** The AI generates code in its "default style" (which is an average of all code it was trained on) instead of matching the project's conventions. The result: inconsistent naming, different import ordering, different error handling patterns.
**How to catch:** Before generating code, read 2-3 existing files in the same module. Note their naming convention, import style, error handling pattern, and comment style. Match them exactly.
