# retry-requires-diagnosis-not-repetition

A /retry after wrong output type must first identify the mismatch cause before re-executing

**Source**: Extracted from task failure (2026-03-25)
**Tags**: retry, task-routing, podcast, audio-generation, error-handling

---

## Rule: Retry ≠ Re-execute

When a user issues `/retry` after receiving the **wrong type of output** (e.g., voiceover instead of podcast, wrong language, wrong format), the agent must **not** simply re-run the same code path.

### What happened here
User asked for podcast audio. Agent generated a voiceover. User complained and issued `/retry`. Agent generated a voiceover again — same wrong output, same wrong code path. The retry loop ran twice before the user had to explicitly spell out the misclassification.

### The rule
Before retrying after a wrong-output-type complaint:
1. **Identify which code path was actually invoked** (was it `generate_podcast()` or `generate_voiceover()`?)
2. **Trace why that path was selected** — what in the task description or state caused it?
3. **Confirm the correct path** before executing

### Signal words that trigger this rule
- "为什么生成 X？我要的是 Y" — explicit type mismatch complaint
- "这不对" + /retry without further instruction
- Any /retry immediately following an output the user rejected

### Secondary issue surfaced
The `No module named 'music'` error on podcast generation means the podcast code path had **unverified dependencies**. Before generating podcast audio for the first time (or after codebase changes), verify all imports resolve. A dry-run import check costs milliseconds and prevents silent failures mid-generation.
