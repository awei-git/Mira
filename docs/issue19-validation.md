# Issue #19 implementation checks — 2026-09-23

Base: `cloud/podcast-api-env` at `062e63921c00cbbbdb476bad334f9b321050405f`.
Implementation branch: `codex/issue19-runtime-wiring`.

## Automated results

- `python -m pytest tests/test_content_worker.py -q`: **23 passed**.
  Checks include private projection rejection, hash/path/symlink boundaries,
  activation rollback, sanitized bundle install to two temporary destinations,
  skill audit refusal before writes, seed filtering/idempotency/forward progress,
  provider failure retention, content-only writer context, outbox date/evidence
  rules, immutable skill counters, and filtering before GitHub blob downloads.
- Broader focused command: `python -m pytest tests/test_content_worker.py
  tests/writer tests/shared/test_writer_gate.py
  tests/memory/test_soul_protected_writes.py tests/memory/test_soul_recall.py -q`:
  **44 passed, 1 failed**. The failure is
  `tests/writer/test_anti_ai_hard_bans.py::test_scan_blocks_any_em_dash`.
- The original writer handler loaded from `git show 062e639:agents/writer/handler.py`
  also returned `em_dash_flagged=False` for both strict/relaxed modes with the
  test's exact input. The failing behavior predates this patch. It was not changed
  as part of runtime wiring.
- Existing writer handler and editorial gate imported successfully; all four
  policy files were present.
- `git diff --check` passed.

Pytest emits an existing `datetime.utcnow()` deprecation warning in skill usage
accounting. Unit fixtures do not constitute a real model or cloud acceptance run.
The local machine-readable focused receipt is at
`data/operator/issue19-phase1/content-worker.xml` (runtime data, not committed).

## Real input check

`python scripts/content_batch.py seeds --scan-only` returned one total seed,
zero eligible seeds, and ignored seed `49a5c4d10716`.

The source branch was checked again on GitHub: the seed remains `candidate`,
without `track`. It has not been silently promoted. The app-approved cloud
identity projection is absent. The ledger proposal comment has no confirmation
yet: <https://github.com/awei-git/Mira/issues/19#issuecomment-5800352775>.

## Acceptance still open

No EC2 stage, live identity switch, real skill package activation, real English
draft, ledger event, Muse presentation, timer installation or publication occurred.
These require the approved projection/eligible seed, Mira's ledger contract
confirmation, host/path reconciliation and the prescribed review/release flow.
Tasks 1–6 are not being marked complete by these local checks.

## Scope adherence and comprehension

Supporting fixes stay within the requested migration: filter private inputs before
upload (rsync-only exclusions are too late), preserve host runtime/configuration,
isolate the content writer from personal recall, keep audit manifests immutable,
and reserve model attempts before expensive calls. Existing function signatures
retain defaults for legacy callers. An identity failure is checked before a paid
attempt reservation; a provider failure after reservation requires deliberate
review instead of an automatic paid retry. Approval binds to exact content in the
proposed ledger; this patch creates no alternate queue or fake delivery receipt.
