# Issue #19: shared obligation ledger — proposed contract

Status: proposed for Mira review; implementation of the shared ledger is gated on
Mira's confirmation per issue #19. This document does not authorize a deployment.
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
Claim is an atomic transaction with revision compare-and-swap. Expired leases on
read-only/draft work may be reclaimed with a recorded attempt; ambiguous external
writes become blocked pending reconciliation, never automatically republished.
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
  "draft_path": "data/drafts/substack_en/example/<version>/draft.md",
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

## Read/write contract

- POST /obligations: create idempotently with kind, owner, payload and key.
- POST /obligations/{id}/claim: actor, expected_revision, lease duration.
- POST /obligations/{id}/transition: actor, expected_revision, status and result.
- GET /obligations: bounded owner/status filters, opaque pagination cursor.
- GET /events?after=<sequence>&limit=<bounded>: durable incremental event stream.
- GET /artifacts/{registered-id}: allowlisted bytes and checksum, authenticated.

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

1. Confirm the ledger contract and its host before implementation (task 3 gate).
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
