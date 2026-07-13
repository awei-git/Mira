# Substack Publish Incident

## Symptom

Mira publishes wrong content, fails to publish, repeats a reply, leaks sensitive material, or bypasses writer/preflight.

## 5-Step Diagnostic

1. Check preflight and sensitivity logs:
   ```bash
   tail -100 logs/publish_preflight_log.jsonl
   tail -50 data/audit/sensitivity_blocks.jsonl
   ```
2. Check activity reply dedup shadow:
   ```bash
   tail -100 data/social/reply_dedup_shadow.jsonl
   ```
3. Run publish guard tests:
   ```bash
   pytest tests/shared/test_preflight.py tests/integration/test_publish_guards.py
   ```
4. Inspect the socialmedia handler path:
   ```bash
   rg -n "preflight_check|publish_to_substack|reply_to_note|_append_reply_shadow" agents/socialmedia
   ```
5. Check the latest task trace/result:
   ```bash
   find data/tasks -name result.json -mtime -1 -print
   ```

## Common Causes & Fixes

- Content was an error string -> preflight should block; if not, add the pattern to `lib/publish/preflight.py`.
- Confidential/Tetra payload -> sensitivity scan should block; verify the audit row and keep the task out of publish flow.
- Duplicate reply -> inspect `reply_dedup_shadow.jsonl`; add the missed reply key to seen state only after confirming.
- Auth failure -> follow `docs/runbooks/oauth-throttle.md`.

## Escalation

If public content is wrong, delete/unpublish manually first. Then freeze publish automation until the guard test that should have caught it exists and passes.
