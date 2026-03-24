# report-delivery-mobile-first

Reports delivered to users must be accessible on mobile; local file paths are useless outside the local machine

**Source**: Extracted from task failure (2026-03-23)
**Tags**: reporting, llm-routing, mobile, fallback, delivery

---

## Rule: Report Delivery Must Be Mobile-Accessible

When generating reports or any output intended for a user who may be on a different device (phone, remote machine), **never deliver only a local file path**. Local paths like `/Users/angwei/Sandbox/...` are inaccessible from any other device.

### What to do instead
- Embed the key content inline in the message (summary, tables, warnings)
- If a full document is needed, upload to a shareable location (cloud storage, email, messaging service)
- For PDF specifically: either inline the critical data as text/markdown, or push to iCloud/Dropbox/similar and share a public link

### LLM Routing Rule
If the primary synthesis LLM (claude CLI) times out or fails:
1. Fall back to a local model first (faster, no network dependency)
2. Fall back to Gemini API as secondary
3. Do NOT deliver a partial report that only says "synthesis failed" — deliver what data you have in degraded mode

### Timeout Handling
- 300s timeout on claude CLI is too long for a report pipeline; set a tighter timeout (60-90s) with faster fallback
- A synthesis failure should trigger fallback, not report failure as the final state

**Never**: `Full report: /local/path/to/file.pdf` as the only delivery mechanism
**Always**: Inline the critical content; paths are supplementary, not primary
