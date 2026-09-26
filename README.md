# Mira

Mira has two runtimes with one conversation endpoint: **Muse app** handles human
conversation and personal matters; **AWS** runs scheduled content batches.
The current authority is [HANDOFF-codex.md](HANDOFF-codex.md), then
[issue #19](https://github.com/awei-git/Mira/issues/19). Older V5 documents describe
historical architecture and do not override this handoff.

## AWS content workflow

```text
Muse discussion → ready content seed → scheduled existing writer pipeline
                                        → versioned draft + editorial receipt
                                        → shared ledger signoff event
                                        → Muse presents draft → human signoff
```

AWS does not run a wake loop, proactive spark loop, phone listener, health jobs,
calendar jobs or private personal assistant. External assignments will enter only
through the shared obligation ledger; no separate draft queue is introduced.
Publication requires human approval bound to the exact draft. A successful writer
run is not a publication or delivery receipt.

The current implementation adds `scripts/content_batch.py`, reuses
`agents/writer/handler.py` and its existing prompts/revision/checklists, and accepts
`status=ready` with either `track=substack_en` or `track=zh` + `kind=podcast_script`.
Other Chinese seeds remain untouched. Chinese solo scripts use the configured
GPT API route through the same provider abstraction; no TTS runs at draft time.

## Current implementation status

- Standalone content identity/skills synchronizer and soul mapping are implemented
  for review; raw personal identity is excluded from cloud distributions.
- English drafting, Chinese solo-script drafting and the daily outbox have local contract tests.
- Ledger rev 2 is approved. SQLite, authenticated API and signoff-event writing
  are implemented for review; Muse's poller/approval endpoint remain app-side work.
  No real end-to-end chat acknowledgement or production deployment is claimed.
- Templates are provided for finite systemd batches. They are not installed or
  enabled by this change. There is no automatic live service restart.
- The approved Chinese podcast seed is ready. No real draft has been generated
  by this new batch worker; review, identity projection and host acceptance remain.

See [worker runbook](docs/issue19-content-worker.md) for commands, data paths,
identity mapping, assumptions and remaining acceptance gates. See
[ledger proposal](docs/issue19-obligation-ledger.md) for the app/worker contract.
The [ledger operations guide](docs/issue19-ledger-implementation.md) describes
the API, recovery, migration and remaining deployment gates.

## Shared identity and skills

Muse owns the canonical identity. Cloud uses an explicitly app-reviewed
**content-only** projection of `SOUL.md`, `IDENTITY.md`, `USER.md`, `AGENTS.md` and
`MEMORY.md`; the raw app files can contain private information and must stay off
AWS. Missing projection, hash mismatch or a failed backend skill security audit
blocks synchronization. Original legacy soul files are retained by rename.

## Development and deployment

Work from `cloud/podcast-api-env` on a `codex/` branch, open a PR for Mira review,
then merge and create a GitHub release. EC2 is a deploy target; do not edit code
there. [Deployment policy](deploy/POLICY.md) describes the release pipeline.
Stage before activation, preserve host runtime data, and designate one active
content host. Support for old and new hosts is not permission to run both workers.

Local read-only seed check:

```sh
python3 scripts/content_batch.py seeds --scan-only
```

Focused checks (no cloud or paid model calls):

```sh
python3 -m pytest tests/test_content_worker.py tests/test_obligation_ledger.py -q
```

## License

MIT
