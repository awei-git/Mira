# Issue 22 task 5: operational event writer

This proposal is stacked on PR21 (`codex/issue19-ledger`). It adds a host-local
receipt writer to the same SQLite ledger and the existing authenticated `/events`
read surface. It does not install a collector, enable a timer, claim that Muse
received a message, or change publication/paid-run authorization.

## Input and event contract

`scripts/record_content_operation.py --database <shared-ledger> --artifact-root
<content-root> --receipt <sanitized-json>` reads one bounded receipt (4 KiB).
The receipt has exactly these fields:

```json
{
  "unit": "tetra-research-evening.service",
  "run_id": "00000000000000000000000000000001",
  "completed_at": "2026-09-23T20:38:45+00:00",
  "outcome": "failed",
  "reason": "source_unavailable"
}
```

The example is synthetic. `run_id` in production must identify the actual
invocation (such as systemd's 32-character InvocationID), retained across retries;
never mint a new ID every poll. A trusted collector must map terminal service and
producer/delivery receipts to the fixed vocabulary. No raw log lines, URLs,
exception text, report content, prompts, or private data are accepted. Rejection
of extra fields is intentional. The writer validates structure, not factual
truth of the supplied receipt; it must not be exposed as an untrusted endpoint.

Allowlisted units are creator, market collect, and Tetra morning/evening research
and mail services and their corresponding timers. Outcomes/reasons are enumerated
in `content_worker.operations.REASONS`. SMTP acceptance of an unaccepted-research
notice must map to `failed/research_unaccepted`, never `produced`. A stopped,
missing or not-yet-run task must not be represented as a completed empty tick.

Every new receipt emits `operation.observed`. Failures and skips also emit
`operation.failed` and `operation.skipped`. Four consecutive explicit `empty`
receipts emit one `operation.no_output` event at the threshold. Later empty ticks
remain observations, without repeated alerts; a non-empty outcome resets the
streak. Missing samples are unknown and are not counted. The threshold is bounded
1–96 and persisted per unit; changing it after ingestion requires a reviewed state
migration so an active streak cannot silently miss its threshold.

Observation, alert and counter commit in one immediate SQLite transaction.
Identical invocation replays return the original event IDs, including after
restart/concurrent ingestion. Changed bytes/config for an existing ID conflict.
New samples must be strictly ordered by completion time within each unit; late
new samples conflict rather than corrupting the consecutive count. Collectors
must replay retained samples oldest-first and surface rejection. Equal timestamps
for distinct invocations are rejected; retain subsecond completion timestamps.

The terminal `operation.observation` obligation is an ingestion receipt: its
`succeeded` status means ingestion completed, not that the underlying service
succeeded. Read `payload.outcome` and alert kind for application health. These
records never enter the queued worker path. Generic bridge creation still cannot
mint this kind. No schema migration, public write route or second queue is added.

## Reader and deployment handoff

Mira's existing poller should consume these three alert kinds using the same
sequence/epoch cursor and event-ID dedupe as draft events. Observations alone need
not create chat messages. Failed/skipped alerts identify the unit, invocation,
time and bounded reason. No acknowledgement or presentation claim is emitted by
the writer. The app owner still owns message rendering and acknowledgement.

Required before task 5 is complete:

1. Review this stacked change and PR21; choose the final deployed source/release.
2. Add the trusted host collector/service wrapper through the deployment pipeline,
   covering timer/service failures, explicit skips and actual creator completion
   receipts. Preserve source receipts and stable invocation IDs. No collector is
   installed by this PR; the cloud source repository does not yet contain this
   shared-ledger writer.
3. Review the threshold and accounting for intentional waits. Four is the proposed
   default, not an assertion that an hour without publication is inherently wrong.
4. Stage a synthetic failure in an isolated ledger, read it through authenticated
   `/events`, and obtain an independent Muse presentation receipt.
5. Only then deploy production monitoring under the agreed timer acceptance gate.

No model, TTS, SMTP, AWS or publishing calls were made by the tests. The test suite
uses real SQLite and the actual bridge API to exercise concurrent dedupe,
transaction rollback, restart-persistent streaks, ordering, input privacy and
cursor retrieval. Regression: 81 passed (five existing UTC deprecation warnings).

Scope adherence: issue22 task5 write-side proposal only. Production collection,
Mira app delivery, deployment, and the task's end-to-end acceptance remain open.
