# verify-output-completeness-before-done

Never declare a writing task complete without confirming the full output was captured, not just the opening

**Source**: Extracted from task failure (2026-04-04)
**Tags**: autowrite, output-integrity, writing, completion-detection

---

## Rule: Verify full output exists before declaring completion

When an autowrite or long-form writing task produces output, the agent must verify the *entire* piece exists before reporting success. The failure mode:

1. Agent generates essay beginning
2. Agent outputs "写好了！终稿如下" (done, here it is)
3. Actual captured output is truncated after the first paragraph
4. Task is marked complete with a fragment

**What to check before declaring done:**
- Does the output contain all planned sections (check against the outline)?
- Does the output end with a conclusion, not mid-sentence?
- Is the word count plausible for the intended piece (1000+ words for a full essay)?

**Root cause:** The agent may have internally generated the full essay but the output channel (task log, write tool, etc.) truncated at a buffer limit. The completion declaration fires before the truncation is detected.

**Fix:** After writing, read back the saved file or output and confirm section count matches the outline. If output ends mid-sentence, treat as write failure and retry — do not emit "done".

**Apply to:** Any autowrite task producing multi-section content. Especially relevant when essay outline has 4+ sections and expected length exceeds 800 words.
