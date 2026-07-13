# OAuth Throttle

## Symptom

Claude Code OAuth starts returning quota, rate-limit, expired-token, 401, or 403 errors. Mira should write `data/auth_state/events.jsonl`, surface the `anthropic_oauth` alert, and stop the affected workflow instead of using an Anthropic API key.

## 5-Step Diagnostic

1. Run auth checks:
   ```bash
   python3 scripts/auth_health_check.py
   ```
2. Inspect OAuth failure events:
   ```bash
   tail -50 data/auth_state/events.jsonl
   cat data/auth_state/anthropic_oauth.json
   ```
3. Verify provider failure-path tests:
   ```bash
   pytest tests/llm_port/test_provider_fallback.py
   ```
4. Check Claude CLI availability:
   ```bash
   which claude
   claude --version
   ```
5. Check current routing:
   ```bash
   sed -n '1,120p' agents/super/runtime/registry/llm_routing.yaml
   ```

## Common Causes & Fixes

- Claude OAuth quota exhausted -> wait for reset; Mira should mark affected Tier 2 workflows failed/blocked and keep local/Tier 1 routes running where explicitly configured.
- Claude CLI binary missing -> fix `CLAUDE_BIN` in config or reinstall Claude Code CLI.
- Repeated OAuth failures -> lower Tier 2 traffic, move routine tasks to oMLX/local routes, or configure an explicit non-Anthropic fallback in `llm_routing.yaml`.

## Escalation

If OAuth is unavailable, pause public publishing and mark affected tasks `needs-input` instead of retrying indefinitely. Do not add `ANTHROPIC_API_KEY` as a workaround.
