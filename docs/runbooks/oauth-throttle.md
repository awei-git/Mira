# OAuth Throttle And Fallback

## Symptom

Claude Code OAuth starts returning quota, rate-limit, expired-token, 401, or 403 errors. Mira should fall back to `anthropic_api` and write `data/auth_state/events.jsonl`.

## 5-Step Diagnostic

1. Run auth checks:
   ```bash
   python3 scripts/auth_health_check.py
   ```
2. Inspect fallback events:
   ```bash
   tail -50 data/auth_state/events.jsonl
   cat data/auth_state/anthropic_oauth.json
   ```
3. Verify provider fallback tests:
   ```bash
   pytest tests/llm_port/test_provider_fallback.py
   ```
4. Check API key availability:
   ```bash
   test -n "$ANTHROPIC_API_KEY" && echo ok || echo missing
   ```
5. Check current routing:
   ```bash
   sed -n '1,120p' agents/super/runtime/registry/llm_routing.yaml
   ```

## Common Causes & Fixes

- Claude OAuth quota exhausted -> wait for reset; Mira should continue with `anthropic_api` for non-tool completions.
- `ANTHROPIC_API_KEY` missing -> restore it from Keychain/shell environment and restart Mira.
- Claude CLI binary missing -> fix `CLAUDE_BIN` in config or reinstall Claude Code CLI.
- Repeated fallback on every request -> lower routine traffic or move routine tasks to oMLX/local routes.

## Escalation

If both OAuth and API fallback fail, pause public publishing and mark affected tasks `needs-input` instead of retrying indefinitely.
