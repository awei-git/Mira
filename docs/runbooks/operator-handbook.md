# Mira Operator Handbook

Entry point for V4 operations. Use this first, then jump to the specific runbook.

## Symptom

Mira looks stuck, misses a publish/task, sends a wrong status, or the iOS app shows stale state.

## 5-Step Diagnostic

1. Check the current plan and live runtime gates:
   ```bash
   cat docs/CURRENT_PLAN.md
   PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_status.py --actions
   ```
2. Check service heartbeat and current task status:
   ```bash
   curl -k -s https://127.0.0.1:8443/api/heartbeat || curl -s http://127.0.0.1:8000/api/heartbeat
   ```
3. Check recent auth state:
   ```bash
   python3 scripts/auth_health_check.py
   tail -50 data/auth_state/events.jsonl
   ```
4. Check task verification and dispatch:
   ```bash
   pytest tests/control/test_projection.py tests/super/test_task_manager.py
   ```
5. Check logs:
   ```bash
   tail -200 data/logs/mira.log
   tail -100 logs/publish_preflight_log.jsonl
   ```

## Common Causes & Fixes

- Control DB unavailable -> run `.venv/bin/python lib/migrations/runner.py`, then restart Mira.
- Duplicate LaunchAgent cycle -> check `data/locks/launchagent.pid`; if stale beyond 5 minutes and PID is dead, remove it.
- Auth expired -> follow `docs/runbooks/oauth-throttle.md`.
- Publish incident -> follow `docs/runbooks/substack-publish-incident.md`.
- Backup/restore concern -> follow `docs/runbooks/restore-drill.md`.

## Escalation

If a failure affects task dispatch, public publishing, identity, memory, or backups, write an incident note in `data/dr/` or `data/audit/` and freeze self-evolution until the root cause is known.
