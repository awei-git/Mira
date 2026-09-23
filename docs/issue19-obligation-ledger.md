# Issue #19: shared obligation ledger — proposed contract

Status: proposed for Mira review; implementation of the shared ledger is gated on
Mira's confirmation per issue #19. Review revision 2 addresses the three gaps
identified in HANDOFF-codex.md; it remains unapproved. This document does not
authorize a deployment.
Source: HANDOFF-codex.md at 062e639; this supersedes the previous autonomous
publisher/always-awake architecture. AWS is a scheduled content worker. Muse Mira
is the human conversation endpoint. Human signoff is required before publication.

## One store, three clients

Use one SQLite database on the active content host:
`/var/lib/mira-obligations/ledger.sqlite3`. The bridge exposes authenticated
operations; Muse and Codex use that API, and AWS uses the same repository methods
locally. SQLite never lives on a network mount. Back up with SQLite's backup API.
Only one host owns the live database; support for both deployment targets does not
mean two independent active ledgers or two publishing workers.

The existing bridge task API becomes a compatibility adapter into this store.
Import existing task IDs once, retain the original files for rollback, and record
an import receipt. Do not silently run two queues. The worker accepts outside work
only through this ledger. A short scheduled poll claims work and exits; it has no
spark, phone listener, private-assistant jobs, or open-ended wake loop.

## Schema

`obligations`: id (UUID/text primary key), kind, owner
(`mira-app|mira-aws|codex`), created_by, status, payload_json, result_json,
idempotency_key (unique), revision, created_at, updated_at, lease_owner,
lease_expires_at, attempts, parent_id. All times are UTC RFC3339.

`obligation_events`: monotonic sequence primary key, event_id (UUID unique),
obligation_id, kind, actor, payload_json, created_at. Append-only history is
written in the same transaction as the state change. Poll consumers retain a
sequence cursor; replay is safe through unique event IDs. No event deletion on
read, no destructive queue pop. Consumers acknowledge only after their side effect
is durably recorded. Chat delivery is Mira's responsibility, not a worker claim.

State transitions: queued -> claimed -> running -> succeeded/failed/blocked;
queued -> cancelled; blocked -> queued only by an explicit operator transition.
Claim is an atomic transaction with revision compare-and-swap. Draft lease expiry
triggers receipt reconciliation first, never an immediate new paid attempt (see
the reclaim protocol below). Ambiguous external writes become blocked pending
reconciliation, never automatically republished.
Terminal result and its artifacts are retained. A worker cannot approve its own
publication by changing an obligation status.

## Draft return and publication gate (tasks 5–6)

A seed job has kind `seed.draft`, owner `mira-aws`, and idempotency key
`seed.draft:<seed_id>:<seed_sha256>:<editorial_policy_sha256>`. Only seeds whose
status is `ready` and track is `substack_en` are eligible. A changed seed/policy
creates a new version, never silently replaces an already approved draft.

After durable draft/artifact writes, emit `draft.ready_for_signoff` with:

```json
{
  "seed_id": "example",
  "draft_path": "data/drafts/substack_en/example/<version>/example-draft.md",
  "draft_sha256": "<sha256>",
  "packet_path": "data/drafts/substack_en/example/<version>/packet.json",
  "policy_sha256": "<sha256>",
  "publication_gate": "human_approval_required",
  "recipient": "mira-app"
}
```

Paths must be relative, within the registered draft root, without symlinks or
traversal. Expose artifact reads through authenticated bridge operations rather
than assuming Muse can open an EC2 filesystem path. The writer records an event
only after the final editorial gates pass; blocked drafts remain available with
a failure report but do not masquerade as ready for signoff. A duplicate poll
must reuse the same job/artifact receipt and not incur another model run.

Muse consumes the event, presents the actual bytes/hash in chat, and records its
own `draft.presented` receipt. Human approval must bind to seed, exact draft hash,
channel and email intent. An app-side agent assertion alone is not human consent.
The publishing worker must consume a distinct authorized publication obligation;
this implementation phase does not enable automatic publication. Any revision
invalidates the old approval. Never infer consent from elapsed time or silence.

## Muse event poller: cursor, presentation and acknowledgement

Muse owns one scheduled poller, every 60 seconds, with a local overlap lock.
Fetch up to 100 events per page, at most five pages per invocation, using the
existing authenticated bridge channel. Polling never removes server events.
Persist `last_committed_sequence`, event IDs, payload hashes and presentation
receipts in Muse's durable local state, not model conversation memory. An empty
page leaves the cursor unchanged. Unknown ledger epoch/database reset is an
operator reconciliation error, not permission to reset to the latest sequence.

For `draft.ready_for_signoff`, validate recipient, event schema and artifact
registration, download the artifact and check its exact byte hash. Stage its
event ID locally before presentation. Present the draft with stable delivery key
`mira-event:<event_id>`; the app's delivery adapter must support idempotent writes
or lookup of an existing message by that key. After a durable app message receipt
exists, submit `draft.presented` with event ID, message ID and draft hash through
the authenticated app endpoint. Duplicate acknowledgements are idempotent;
different hashes/message receipts for the same key conflict.

Commit the local processed-event record and cursor together only after server
acknowledgement. A crash between presentation and acknowledgement reuses/looks up
the original message and resends the acknowledgement; it never blindly presents
again. If Muse cannot reconcile an uncertain delivery, hold that event for
operator review. Do not claim exactly-once delivery without that adapter contract.
Irrelevant events can be recorded as ignored and committed; malformed relevant
events block advancement until an explicit, durable operator skip/reconciliation.

Transport failures retry on later invocations with exponential backoff starting
at 60 seconds, capped at 15 minutes with jitter. Preserve cursor and pending
receipts; do not resend chat messages as a networking retry. Five consecutive
failures produce one actionable alert, recovery clears it. Authentication errors
pause delivery and alert immediately. The app poller adds no second task queue:
its local tables hold consumption/delivery bookkeeping only.

## Human approval and publication authorization

Use a separate trusted app authorization endpoint, inaccessible to writer/model
credentials, to mint a publication obligation. The LLM agent may request that
Muse display approval controls; it cannot supply the human authorization proof.
The app authenticates a real human UI action (or a verified human chat reply to
that exact presentation), not an agent-generated message claiming consent.
If Muse cannot provide this trusted interaction binding, publishing stays blocked.

The approval request binds: original draft obligation ID, seed ID, immutable
artifact ID, SHA-256 of **exact UTF-8 draft bytes**, editorial policy hash, channel,
destination publication, email-send intent/audience, one-use nonce and expiry
(24 hours). Display the specific version and email intent to the human. Retain
only an opaque interaction/approval receipt on AWS; do not copy personal chat
history or the human's name. Role-scoped credentials must replace the shared
bridge token for this endpoint before enabling it.

The trusted authorizer transaction verifies that the artifact/hash is still the
current draft, editorial gates still pass, the presentation receipt matches, the
nonce is unexpired/unused and the human interaction is authentic. It then stores
the immutable approval receipt and creates `publication.authorized`, owner
`mira-aws`, linked to that approval and exact artifact, with a unique idempotency
key. Only this endpoint can create that kind; generic obligation creation rejects
it. Worker credentials can consume but cannot mint or alter approvals.

Immediately before publishing, re-fetch and hash the immutable artifact and check
approval revocation, expiry, target and email intent. Any revision creates a new
artifact/version, revokes pending old approvals and blocks queued publication of
that old version in the same ledger transaction. Rerendering may not substitute
different draft text. A timeout after a possible external publish is reconciled
against a publisher receipt before any retry; consent does not authorize duplicate
publication. Public URLs and final publisher receipts become ledger events.

## Draft lease expiry: reconcile before spending again

Lease expiry permits investigation, not another model call. Atomically acquire a
reconciliation lease with a new fencing revision, then inspect the attempt's
durable receipt and registered artifacts by its original idempotency key:

| Observed evidence | Permitted action |
| --- | --- |
| Completed draft, matching hashes and passed gates | Reuse it; commit result/missing signoff event idempotently, with zero new model calls |
| Completed but editorial-blocked draft | Retain it as blocked; no automatic paid revision |
| Running/incomplete receipt or uncertain provider outcome | Block pending reconciliation; do not restart the paid pipeline |
| Explicit failure after a paid attempt | Retain cost/attempt receipt; a reviewed retry requires a distinct authorized attempt |
| Missing/corrupt/unreadable receipt or mismatched artifact | Block unless the ledger proves no paid work began; missing data alone is not proof |
| Authoritative ledger says no paid step began and storage is healthy | Dispatch once under the new lease, using the pre-call protocol below |

Before **each** paid step, the worker durably writes a reservation receipt with
obligation, attempt and step IDs, input hash, provider request/idempotency ID where
available, and lease revision. It then atomically records `model_step.started`
under that same fencing revision in the ledger **before** sending the request.
A stale lease cannot authorize that transition. If a crash occurs between these
records or after dispatch but before recording the response, treat it as uncertain;
use a provider status/receipt if available, otherwise require operator resolution.
Never rely on a missing response as evidence that billing did not happen.

On success retain output and cost/usage receipts, then reconcile job completion
and `draft.ready_for_signoff` in an idempotent transaction. A crash after saving a
draft but before writing its event must replay only the event, not the writer.
Fault-injection acceptance must cover that boundary, the pre-call reservation
boundary, a timed-out provider response, concurrent reclaim and a stale worker.
This is a required future ledger/worker integration contract, not an assertion
that the current writer already implements per-step reservations.

## Read/write contract

- POST /obligations: create idempotently with kind, owner, payload and key.
- POST /obligations/{id}/claim: actor, expected_revision, lease duration.
- POST /obligations/{id}/transition: actor, expected_revision, status and result.
- GET /obligations: bounded owner/status filters, opaque pagination cursor.
- GET /events?after=<sequence>&limit=<bounded>: durable incremental event stream.
- GET /artifacts/{registered-id}: allowlisted bytes and checksum, authenticated.
- POST /events/{event-id}/presented: app-only idempotent presentation receipt.
- POST /human-approvals: trusted human authorizer only; atomically records approval
  and creates the exact-version publication obligation.

Keep existing bridge authentication; no credentials in payloads, logs or GitHub.
Split service credentials/roles before granting untrusted agents access: the draft
worker cannot mint an approval or mutate identity/budget. Validate body sizes,
known kinds, ownership, transitions and artifact boundaries server-side. Repeated
keys with different payload hashes return a conflict, never overwrite work.

## Outbox is a view, not another queue

`data/outbox/YYYY-MM-DD.md` contains exactly `journal`, `verified learnings`,
`需同步事项`. It summarizes activity for the New York calendar day, with receipt
references. Unverified ideas stay in journal; only independently checked outcomes
may be listed as verified learnings. It does not replace `draft.ready_for_signoff`
events or claim that a draft was delivered to chat. Reruns reproduce the day's
view from explicit input records instead of duplicating entries.

## Migration and acceptance

1. Mira confirms this schema/API/event contract and the active ledger host.
2. Add tested create/read/claim/state transitions and append-only events. Cover
   concurrent claims, replay, stale revisions, lease expiry and unknown writes.
3. Stage an existing GitHub release with `deploy.py mira <tag> --stage`; no live
   code/data modification during stage or standalone identity/skills sync.
4. Run one real eligible seed through the existing writer pipeline; save the
   draft, editorial packet, hashes, costs and real execution receipt.
5. Record its event once; Mira independently acknowledges/presents it in chat.
6. Cut the bridge compatibility adapter over, retaining original tasks for rollback.
   Do not enable any publication worker without exact-content human approval.

## Decisions requested from Mira

1. Confirm this revised contract (Muse poller, trusted approval minting and
   receipt-first reclaim) before implementation (task 3 gate). Mira review names
   `mira-content` as the live target; actual checkout path remains to be checked.
2. `identity/USER.md` and `identity/MEMORY.md` contain private personal material.
   Raw sync conflicts with the handoff's no-health/calendar/family-data rule.
   Please provide an app-maintained content-only projection of the same five files
   (suggest `identity/cloud/`) or approve a field/section export contract. Until
   then the sync must fail closed; no private source files are copied to EC2.
3. The only seed at 062e639 is `49a5c4d10716`, status `candidate`, with no `track`.
   Please supply/promote a seed with `ready` + `substack_en`, including the source
   evidence needed for first-person claims. Codex will not promote it implicitly.
4. The issue requests acceptance on both hosts; previous user instruction retires
   the old host. Support both targets without turning on the old host. Confirm
   whether its acceptance can be staging-only when access is next available.
