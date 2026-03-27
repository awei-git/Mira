# suppress-chinese-spinner-artifacts

Detect and strip CJK loading/spinner artifacts before emitting output

**Source**: Extracted from task failure (2026-03-26)
**Tags**: output-quality, cjk, artifact-detection, streaming

---

## Rule: Strip CJK Spinner/Loading Artifacts from Output

The pattern `还在转` (lit. "still spinning") is a Chinese-language UI loading indicator that leaks into output when:
- A streaming response is interrupted mid-generation
- A UI component's placeholder text gets captured instead of the actual content
- A tool or API returns a loading state rather than a completed result

**What to check before emitting output:**
1. Scan output for known CJK spinner/loading patterns: `还在转`, `加载中`, `请稍候`, `正在处理` and their variants
2. Also check for equivalent English artifacts: `loading...`, `please wait`, `processing...` that may indicate a captured intermediate state
3. If detected, treat the output as incomplete — do not pass it downstream or present it to the user

**Corrective action:**
- Retry the upstream call that produced the artifact
- If retries consistently return artifacts, escalate: the upstream source is returning loading states instead of content
- Log the artifact pattern for quality monitoring

**Why this matters:**
These artifacts are silent corruption — they look like content but represent a failure to capture completed output. Passing them downstream poisons dependent steps without obvious error signals.
