# Shared ledger implementation — tasks 3 and 6 writer side

Authority: HANDOFF at `9149868`, after PR20 merge and explicit rev2 approval.
The subsequent HANDOFF at `cf9d3c8` adds task 7 (Chinese podcast scripts).
That extension now uses the same writer, paid-step guard and signoff event,
with `track=zh` and `kind=podcast_script`. Local fixture checks do not mean the
real episode has been generated or delivered.
This is code for review, not a deployment receipt. The live target is
`mira-content`; the retired host has staging-only acceptance. Timers stay off
until the real seed → writer → event → Muse presentation acceptance passes.

## One store and two entry points

`lib/obligations.py` is the canonical stdlib SQLite repository. The content worker
imports it locally; the standalone bridge distribution includes exactly that
same tagged source file via `extra_files`, not a second implementation.

Default database: `/var/lib/mira-obligations/ledger.sqlite3`.
`MIRA_LEDGER_PATH` overrides it for controlled staging/tests. Set
`MIRA_ARTIFACT_ROOT` on the bridge to the **same checkout root** used by the writer.
Run bridge and writer under the same designated OS service account: DB files are
mode 0600 and a newly created state directory is 0700. Do not point production
and staging at the same database. Do not mount SQLite on a network filesystem.

Tables: `obligations`, append-only `obligation_events`, registered `artifacts`,
`model_steps`, and version/epoch metadata. Every writer uses short `BEGIN IMMEDIATE`
transactions, WAL, full synchronous commits, explicit connection cleanup and a
plain-text audit under the database directory. Filesystem receipts and the DB
cannot commit atomically together, so reconciliation handles each boundary.

## Authenticated bridge contract

The existing `/bridge/` prefix and bearer/X-Bridge-Token authentication remain.
Host-local `.token` identifies `codex`; distinct optional `.app-token` and
`.worker-token` identify `mira-app` and `mira-aws`. They must not reuse the same
secret. Identity comes from the credential, never a caller-supplied `actor` field.
Protect these files and serve the bridge behind the existing TLS endpoint.

Implemented endpoints:

| Endpoint | Behavior |
| --- | --- |
| POST /obligations | Idempotent create; kind, owner, payload, idempotency_key |
| GET /obligations | Bounded owner/status listing with stable next_cursor |
| GET /obligations/{id} | Full current row/revision |
| POST /obligations/{id}/claim | expected_revision, lease_seconds; owner only |
| POST /obligations/{id}/transition | expected_revision, status, result; actor/state/fence checks |
| GET /events | after sequence, limit, epoch; replayable ascending events |
| GET /artifacts/{id} | Registered draft bytes, X-Content-SHA256; no arbitrary path reads |
| POST/GET /v1/tasks | Legacy shape backed by the same obligation tables |

Requests are bounded JSON (64 KiB, Content-Length required); extra input fields
are rejected. Conflict is HTTP 409, ownership violations 403, missing records 404.
Only `agent`, `ping`, `seed.draft` creation is supported. Neither a worker nor
generic creation can mint `publication.authorized`; `/human-approvals` is absent
and must be implemented by Mira's trusted app integration. Muse also owns its
poller and presentation acknowledgement. The server provides the read surface;
no app poller is bundled or silently scheduled here.

Seed obligation payload is exactly the versioned input reference: seed_id,
seed_sha256, policy_sha256, draft_dir; Chinese scripts also carry track/kind.
Missing track/kind retains legacy English semantics. The canonical idempotency key is
`seed.draft:<seed_id>:<seed_sha256>:<policy_sha256>`. The directory is computed from
those hashes and the seed ID. Queue execution requires the same deployed ready
eligible English/Chinese seed and policy bytes; remote payloads cannot substitute ad-hoc prompts
or newer source into an older task. Other legacy agent work remains visible but
is not executed by this content-only writer.

## Dispatch, recovery and exact artifacts

`scripts/content_batch.py seeds` registers and attempts one current ready eligible
seed. `scripts/content_batch.py queue` polls **existing** matching obligations once
and exits; it never creates another external queue or an always-awake loop.
The latter can eventually run on the approved short timer, after end-to-end
acceptance. No timer is installed or enabled by this change.

Claims are transactional with an expected revision. State transitions advance
that revision. A lease lasts at most three hours and paid step reservations
extend the owning attempt's lease; old revisions cannot mutate the new attempt.
An expired task is never claimed directly. Reconciliation first inspects the
durable draft packet/receipt and paid-start ledger state:

- A valid completed packet commits only its missing event; no model call.
- An incomplete paid attempt, missing/corrupt receipt or unknown provider outcome
  becomes blocked. A missing file is not evidence that the model was never billed.
- No paid step, no receipt and readable input storage permits requeue under a
  newer fence. Old workers' reservations are rejected before dispatch.
- An editorial failure stays blocked and produces no ready event.

The existing writer's provider-route entry point is guarded only inside the
finite content process. Each invocation gets an atomic reservation file, then
`model_step.started` before dispatch, and a response hash/receipt on completion.
Writer threads share the same process guard. Timeout/empty response prevents new
dispatch or automatic fallback in that attempt; already dispatched parallel calls
may still complete. Calls outside a content guard preserve legacy routing.
This is one receipt per provider-route invocation, not a claim to expose every
internal provider HTTP probe/request. Request IDs and token/cost usage are null
when the existing adapter does not supply them; normal provider usage logs remain
the billing reference. No cost or billing success is invented. The $200 combined
monthly budget still requires operational provider/infrastructure limits before
paid timers are enabled.

Draft bytes, packet and receipt land before the completion transaction. The
transaction registers the exact SHA-256 artifact and writes one
`draft.ready_for_signoff` event using a unique dedupe key, then marks the task
succeeded. An identical replay returns the same event; conflicting bytes are
rejected. File mutation is also checked when the API serves a registered artifact.
`draft_ready_for_signoff` means the **event exists**, not that Mira presented it
or the human approved it. The draft worker cannot publish.

## Migration and rollback

Do not deploy this PR directly. Review → merge → GitHub release/tag → staging
comes first. Reconcile actual live checkout paths and service users before the
production bridge is restarted; the old registry target is not authority to
restart the retired host. Stage preserves the live task directory and tokens.

Stop the old file-queue consumer before the one-time import. Select only existing
cloud content task files; do not import local personal tasks, health or portfolio.

```sh
python3 scripts/obligations.py --database /var/lib/mira-obligations/ledger.sqlite3 \
  --artifact-root /path/to/Mira import-legacy --file /opt/mira-bridge/tasks/<id>.json
```

Original IDs, results, timestamps and source hashes are retained. Source files
are untouched. Identical import is idempotent; changed source bytes conflict.
Interrupted legacy work becomes blocked, not queued for repeat execution. New
legacy API requests write only to SQLite. Preserve old files for rollback, but
do not run the old consumer beside the new ledger API.

Use the same CLI with `backup --destination <new-path>` for SQLite's online backup
API; it refuses to overwrite an existing backup. `reconcile --id <id> --revision
<revision>` performs receipt-only reconciliation and never invokes a model.
`list` and `events` expose operator state. Blocked paid attempts require explicit
operator review and a newly authorized seed version before another paid run;
there is no generic force-retry switch.

Rollback is not just restoring old JSON: stop new writes, back up the DB, reconcile
any post-cutover ledger tasks, then restore the prior release/consumer. Preserve
the ledger so no accepted task or paid-attempt record disappears. Do not resume
an ambiguous old job as if it never ran.

## Checks and remaining acceptance

Automated local checks exercise two concurrent claimers, revision/role rejection,
append-only history, event cursor replay/epoch mismatch, provider timeout without
fallback, threaded reservations, receipt loss, saved-draft/event-commit failure,
artifact tampering/symlinks, legacy import and backup, API auth, and the exact
deployed-seed queue boundary. All provider calls in tests are fixtures.

Local regression command (2026-09-23):

```sh
python -m pytest tests/test_obligation_ledger.py tests/test_content_worker.py \
  tests/test_ledger_distribution.py tests/test_llm_routing_policy.py tests/writer \
  tests/shared/test_writer_gate.py tests/memory/test_soul_protected_writes.py \
  tests/memory/test_soul_recall.py -q -k 'not test_scan_blocks_any_em_dash'
```

Result: **96 passed, 1 deselected**, with five existing UTC deprecation warnings.
Task7 follow-up adds `tests/test_podcast_seed_worker.py` to that command:
**106 passed, 1 deselected**, same five warnings. The real seed scan selects the
one ready Chinese script from three source records; it invokes no model. Checks
include same-ledger Chinese artifacts/events, English-language-rule isolation,
actual dispatcher API routing with mocked calls, thread routing, failure gates,
outbox inclusion and keeping editorial disclosure out of spoken text.
The deselected em-dash case is the independently reproduced pre-existing failure
recorded in `issue19-validation.md`; this change does not modify that style rule.
Distribution tests exercise the real archive builder with mocked GitHub/AWS,
check canonical source is fetched at the exact release SHA, reject excluded or
escaping extra paths, and import the unpacked API in a fresh interpreter outside
the repository. Both operator and queue CLI help commands also exit successfully.

Still required: Mira review of this implementation, her content-only identity
projection, release/staging, host/provider/budget readiness,
one real draft and ledger event, and **Mira's independent app presentation receipt**.
Only after those succeed may production timers be considered. No real model
draft, cloud event, Muse acknowledgement or deployment is asserted here.
