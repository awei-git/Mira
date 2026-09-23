# Issue #19 content worker: implementation and acceptance

Authority: `HANDOFF-codex.md` at `062e639`. Branch:
`codex/issue19-runtime-wiring`, based on `cloud/podcast-api-env`.
This runbook describes code for review, not an assertion that EC2 was updated.

## What is implemented

| Task | Implementation | Remaining acceptance |
| --- | --- | --- |
| 1. Shared sync | Independent audited snapshots, explicit activation, package exclusions | App-approved projection; real backend audits; stage on both hosts |
| 2. Soul | Shared five-file mapping, original bytes retained by rename | Activate approved assets and observe actual writer identity |
| 3. Ledger | Schema/API/event proposal in issue #19 | Mira confirmation, then implementation and bridge cutover |
| 4. Daily outbox | Receipt-derived New York daily summary, three sections | Install timer after review; Muse reads and merges |
| 5. Seed writing | Strict English eligibility; existing handler/pipeline; versioned artifacts | Real eligible seed, content identity, provider/budget readiness; real draft |
| 6. Draft return | Event contract and payload documented; draft packet ready | Ledger implementation, event write, Muse acknowledgement |

No real model run, publication, ledger event, timer activation or EC2 deployment
is claimed by this PR. At the source commit the only seed `49a5c4d10716` is
`candidate` and has no `track`; the read-only scan correctly selects zero.

## 1. Shared assets, separately from code

The app canonical `identity/USER.md` and `identity/MEMORY.md` contain private
material. Do not copy raw `identity/` to EC2 or put it in an S3 release archive.
Muse must maintain `identity/cloud/` containing the same five file names and a
manifest with `scope: content_only`, `approved_by: mira-app`, and a `sha256`
object mapping **all five names** to their file hashes. Approval is a reviewed
repository artifact, not an LLM assertion that content is safe. Until that
projection exists, synchronization deliberately fails before destination writes.

From a locally reviewed, tagged checkout of this same repository/branch:

```sh
python3 deploy/sync_shared.py --source /path/to/reviewed-release --destination /tmp/mira-assets
```

The synchronizer audits every registry package, including scripts, through
`memory.soul_skills.audit_skill` before saving assets. Any finding, exception,
review requirement or non-pass blocks the whole sync. It does not auto-enable
an exception for convenient network tools. Actual registry audit results remain
to be collected after the projection is available.

Transport only the resulting `releases/<snapshot-hash>/` directory to a private
staging directory on the target, retaining the hash directory name. Use the
existing authenticated deployment channel, never a public bucket or raw clone.
The bundle contains sanitized identity, audited registry content and integrity
metadata. Keep its originating GitHub release SHA with the operator receipt.
On either box, using the synchronizer from the reviewed release:

```sh
python3 deploy/sync_shared.py --snapshot /private/staging/<snapshot-hash> \
  --destination /var/lib/mira-shared --audit-root /path/to/Mira
```

That command checks exact content hashes/file set and repeats the backend skill
audit before destination saves. It changes no live code or active pointer.
Activate separately with `--activate --legacy-soul /path/to/Mira/data/soul`.
Run as the deployment operator; make assets readable to the service account and
keep the synchronization destination writable only by that operator.

Both the old `mira-ops-box` and new `mira-content` can use this path-independent
command. Do not restart the retired box merely to complete a checklist, and do
not activate two content workers. Mira must confirm the one live ledger/worker
host and current checkout path before live installation.

Activation renames existing `identity.md`, `worldview.md`, `memory.md` and
`interests.md` to `.legacy-<content-hash>` alongside their original location.
No original bytes are uploaded or deleted. Existing backup collisions fail
closed. A failed activation restores renamed files and the previous pointer.
To roll back a successful cutover, stop the finite worker, restore the previous
pointer or unset `MIRA_SHARED_ROOT`, restore the retained legacy filenames, then
start only the intended runtime. Keep the audit log and both generations.

## 2. Actual load paths

With `MIRA_SHARED_ROOT=/var/lib/mira-shared`, a process checks `current.json` and
the snapshot manifest. No shared root means the legacy local paths remain in
use; the **content writer refuses to run without the shared root**.

| Existing soul field | Shared content-only source |
| --- | --- |
| identity | `identity/SOUL.md` + `identity/IDENTITY.md` |
| worldview | `identity/AGENTS.md` |
| memory | `identity/MEMORY.md` |
| interests | `identity/USER.md` |
| skills | `skills/index.json` summary |

These are compatibility mappings, not rewrites of Muse identity. Existing
`memory.soul_skills` reads flat `skills/<name>.md`; the sync therefore supplies
that index/layout while retaining complete packages at `skills/<name>/`.
Skill usage counters go to runtime state so reads do not mutate the audited
index. A snapshot change takes effect on the next finite process; do not switch
identity underneath a running batch.

The content writer bypasses personal thread history, private persona retrieval
and writing RAG. Its persona comes only from the content projection. The normal
legacy writer route remains available to its existing callers. Model/provider
selection remains in the existing abstraction/configuration; no new model is
hardcoded.

## 3. English seed batch

```sh
python3 scripts/content_batch.py seeds --scan-only
MIRA_SHARED_ROOT=/var/lib/mira-shared python3 scripts/content_batch.py seeds
```

Only `ready` + `substack_en` is accepted. A batch attempts at most one **new**
seed/policy version and exits. Already attempted versions, including interrupted
paid work, do not repeat automatically and do not starve subsequent seeds.
The manifest and policy inputs must come from the reviewed release of the same
branch. Seed updates require the next reviewed release; this patch does not add
an unauthenticated pull/webhook or a second external work queue.

The input includes the constitution, voice guide, Substack editorial README and
existing English framework. It calls `agents/writer/handler.py`, including the
existing planning, writing, revision and checks. Evidence references must be
under `seeds/evidence/` with matching SHA-256. Seed text is source material, not
proof of a first-person experiment. The deterministic editorial gate is a filter,
not proof of truth or literary quality; human approval is still mandatory.

Artifacts:

```text
data/drafts/substack_en/<seed_id>/<seed-policy-hash>/
  receipt.json          # written before model calls; retained after failure
  pipeline/             # existing writer intermediate outputs
  output.md             # existing writer final output
  <seed_id>-draft.md    # final versioned draft
  packet.json           # seed/policy/draft hashes and editorial gate report
```

A passing local draft currently has status `awaiting_ledger_contract` and
`ledger_event_written=false`. It is not delivered to Muse. A failed editorial
gate has status `editorial_blocked`; provider/handler failures have `blocked`.
Receipts retain exception types without potentially secret provider error text.
Changed artifact bytes are rejected when an existing receipt is opened directly.

Idempotency currently binds seed and editorial policy hashes. A model, identity
or code update alone does not cause another paid attempt. Explicit operator
review is required for retries; keep the old receipt. Per-seed file locking
prevents duplicate writes. No automatic external side effects are retried.

## 4. Outbox and scheduling

```sh
python3 scripts/content_batch.py outbox --day 2026-09-23
```

Writes `data/outbox/2026-09-23.md`, with exactly `journal`, `verified learnings`,
and `需同步事项`. Default records come from actual worker receipts using the New
York calendar day. The collector does not label its own ideas as verified. An
optional `--records <json-file>` accepts those three lists (JSON keys `journal`,
`verified_learnings`, `sync`). Verified entries require an independent-check
label and matching local evidence hash; checking that reference does not itself
establish the truth of arbitrary prose. Muse owns reading/merging the outbox.

`deploy/systemd/` provides **uninstalled** oneshot/timer templates: seed batches
09:00 and 15:00 New York, outbox 23:55. No missed-run catch-up storm, no wake loop,
no automatic publication. Adjust the service account, checkout and Python paths
to the confirmed host before installation. Provider credentials and monthly
spending controls must be in place before enabling paid batches. One attempt per
invocation is a bound on work, **not** a hard enforcement of the $200 monthly
combined infrastructure/model budget; existing writer work uses multiple model
passes. Do not activate paid timers until that operational budget check is done.

## 5. Deployment and review gates

Mira review → merge → GitHub release/tag → `deploy.py mira <tag> --stage` →
host acceptance → one-host activation. No live code edits. The code packager
excludes raw identity, registry skills and runtime/private data **before upload**;
shared assets travel only through the independently audited bundle. Host runtime
data/configuration/Python environment are retained by rsync exclusions.

The existing registry still names the old Mira path. Do not infer live-host
ownership from it; reconciling the actual host/path is an acceptance gate. This
PR removes the implicit `mira-substack` service restart. The operator must disable
the old wake-loop service/timers on cutover before enabling the finite batches.
Historical supervisor modules remain for compatibility with local callers; the
new cloud entry point imports and starts none of them.

Task 3 remains design-gated at
[the issue comment](https://github.com/awei-git/Mira/issues/19#issuecomment-5800352775).
After Mira approves, implement the one ledger and emit `draft.ready_for_signoff`
only for passed drafts, then obtain Mira's independent `draft.presented` receipt.
Until then there is deliberately no pretend SQLite implementation or parallel
event file queue.

## Scope and validation

Scope: issue19 identity/skills wiring, outbox, English draft batching and design
of the shared event contract. Required supporting changes: private-data exclusion
at packaging time, deployment preservation, writer context isolation and removal
of obsolete cloud-startup README instructions. No health/Tetra changes, external
publication, identity invention or skill security bypass.

Run `python3 -m pytest tests/test_content_worker.py -q` for contract checks. They
use temporary fixture drafts and no paid model/AWS calls. They cannot establish
EC2 readiness, actual skill audit success, prose quality or chat delivery.
