# WA Daily Check

## Symptom

This is the daily five-minute health check, not an incident-only runbook.

## 5-Step Diagnostic

1. Read current V2 gates:
   ```bash
   python3 -m agents.super.cli.v2_status --gates
   ```
2. Check auth and TLS:
   ```bash
   python3 scripts/auth_health_check.py
   ```
3. Check backup state:
   ```bash
   ls -lt ~/MiraBackup/postgres/hourly | head
   ```
4. Check recent task truthfulness:
   ```bash
   pytest tests/control/test_projection.py tests/super/test_task_manager.py
   ```
5. Check public-output guards:
   ```bash
   pytest tests/shared/test_preflight.py tests/socialmedia/test_activity_inbox_shadow.py
   ```

## Common Causes & Fixes

- No hourly backup today -> run `python3 scripts/hourly_pg_backup.py` and inspect the error.
- Auth check warning -> follow `docs/runbooks/oauth-throttle.md`.
- V2 gate regressed -> inspect the changed file, do not mark done without a passing check.
- iOS thread/task stale -> restart bridge service and verify the Postgres task row before rebuilding the app.

## Escalation

If any Tier A gate is red for more than one day, stop feature work and fix the gate first.
