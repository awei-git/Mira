# V3.1 North-Star Remaining Gates

Date: 2026-05-21

This handoff is generated from the live dashboard snapshot. It is a closure checklist for external or operator-gated work; it must not be used to invent feedback, publication, or provider evidence.

Regenerate it from current state:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_remaining_gates.py --date 2026-05-21 --output /Users/angwei/Sandbox/Mira/docs/v31-north-star-remaining-gates-2026-05-21.md
```

Prepare all local no-network closure packets for the current external-feedback, publication-review, and briefing-feedback queues:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_north_star_closure_packets.py --json
```

## Current Live Scorecard

- strategic_score: `0.85`
- public_writeups: `1`
- external_feedback_events: `0`
- briefing_feedback_items: `0`
- briefing_feedback_coverage_rate: `0.0000`
- watch_gates:
  - `external_feedback_below_standard:0/3`
  - `briefing_feedback_missing`
  - `provider_production_readiness_blocked`
- implementation_blockers: `Provider Production Readiness (blocked_external; failed=provider_production_readiness)`

## Gate 1: External Feedback Below Standard

Standard: record at least three concrete external feedback events. Current count: `0/3`.

Recorded public writeup awaiting feedback:

- slug: `v31_green_dot_is_not_evidence`
- published URL: `https://uncountablemira.substack.com/p/198208037`
- current stats snapshot: comments `0`, likes `0`, restacks `0`, views `0`

Prepare a feedback solicitation packet:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_public_feedback_packet.py --slug v31_green_dot_is_not_evidence --published-url https://uncountablemira.substack.com/p/198208037 --stats-artifact /Users/angwei/Sandbox/Mira/data/social/publication_stats.json --json
```

Prepare feedback solicitation packets for every recorded writeup still missing feedback:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_public_feedback_packet.py --all --json
```

After a real external source exists, record it. Replace `<source>` with a concrete, attributable source such as a comment URL, reply URL, review identifier, or customer-discovery reference:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_public_feedback.py --slug v31_green_dot_is_not_evidence --feedback-source <source> --published-url https://uncountablemira.substack.com/p/198208037 --json
```

If a feedback packet has been prepared, prefer recording from its packet metadata:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_public_feedback.py --packet /Users/angwei/Sandbox/Mira/data/v3/artifacts/public_feedback_packets/v31_green_dot_is_not_evidence/136a794141c7/feedback_packet.json --feedback-source <source> --json
```

Do not record generic engagement, internal notes, or placeholder sources as feedback.

Prepare an independent customer-discovery packet for feedback that is not tied to a public writeup:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_customer_discovery_packet.py --topic a2a_trust_manifest --json
```

After a real customer-discovery source exists, record it. Replace `<source>` and `<insight>` with concrete external evidence:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_customer_discovery_feedback.py --source <source> --insight <insight> --json
```

If a customer-discovery packet has been prepared, prefer recording from its packet metadata:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_customer_discovery_feedback.py --packet /Users/angwei/Sandbox/Mira/data/v3/artifacts/customer_discovery_packets/a2a_trust_manifest/6ee9815b4bcb/customer_discovery_packet.json --source <source> --insight <insight> --json
```

Draft that can become another public feedback surface after operator publication review:

- slug: `a2a_manifest_note`
- title: `A2A Trust Manifests Need Receipts, Not Vibes`
- draft: `data/v3/artifacts/a2a_trust_experiment/a2a_trust_experiment_344a748f35cd/a2a_public_writeup_draft.md`
- preview hash: `1b9fcd57bb457f8188728cd19ef9b94d82e2b92fa7337018a5ef9e787aca3ef7`

Safety check:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_public_writeup_safety.py --draft-artifact data/v3/artifacts/a2a_trust_experiment/a2a_trust_experiment_344a748f35cd/a2a_public_writeup_draft.md --json
```

Prepare the publication packet:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_public_writeup_packet.py --slug a2a_manifest_note --draft-artifact data/v3/artifacts/a2a_trust_experiment/a2a_trust_experiment_344a748f35cd/a2a_public_writeup_draft.md --expected-preview-hash 1b9fcd57bb457f8188728cd19ef9b94d82e2b92fa7337018a5ef9e787aca3ef7 --json
```

After publication and at least one concrete external feedback source, record evidence. Replace `<url>` and `<source>` with the actual public URL and feedback source:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_public_evidence.py --packet /Users/angwei/Sandbox/Mira/data/v3/artifacts/publication_packets/a2a_manifest_note/1b9fcd57bb45/publication_packet.json --published-url <url> --feedback-source <source> --json
```


## Gate 2: Briefing Feedback Missing

Standard: at least one operator feedback event on the current weekly blind-sample queue, then at least two promoted briefing items once feedback exists.

Current first queue item:

- item id: `briefing_item:intelligence_briefing_047c5645995d:1:0f557ebcfd57`
- topics: `a2a`
- matched interests: `interest:a2a`
- available buttons: `useful, too_obvious, surprising, wrong, follow_up, pursue_research, pursue_article, not_useful`

Prepare a local review packet for the item:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_briefing_feedback_packet.py --item-id briefing_item:intelligence_briefing_047c5645995d:1:0f557ebcfd57 --json
```

Prepare local review packets for the full current blind-sample queue:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_briefing_feedback_packet.py --all --json
```

Record operator feedback by replacing `<button>` with one of the available buttons:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_briefing_feedback.py --item-id briefing_item:intelligence_briefing_047c5645995d:1:0f557ebcfd57 --button <button> --json
```

If a briefing packet has been prepared, prefer recording from its packet metadata:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_briefing_feedback.py --packet /Users/angwei/Sandbox/Mira/data/v3/artifacts/briefing_feedback_packets/889102d9d57a/briefing_feedback_packet.json --button <button> --json
```

The dashboard exposes `5` weekly blind-sample items. Use the dashboard queue or `v3_status --json` to choose additional items and avoid repeatedly scoring only the first sample.

## Gate 3: Provider Production Readiness Blocked

Standard: provider readiness must pass with real endpoint/token-backed resolver or adapter configuration before production canaries run.

Current global readiness:

- status: `blocked_external`
- readiness findings: `28`
- missing env vars: `28`
- smallest current canary scope: `tts`
- scoped missing env vars: `MIRA_TTS_ADAPTER_ENDPOINT, MIRA_TTS_ADAPTER_TOKEN`

Regenerate the no-secret full provisioning template:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_readiness.py --root /Users/angwei/Sandbox/Mira --write-env-template /Users/angwei/Sandbox/Mira/data/v3/provider_provisioning.template --overwrite-env-template --json
```

Regenerate the no-secret runbook:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_readiness.py --root /Users/angwei/Sandbox/Mira --write-runbook /Users/angwei/Sandbox/Mira/data/v3/provider_provisioning.runbook.md --overwrite-runbook --json
```

Regenerate the scoped canary template:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_readiness.py --root /Users/angwei/Sandbox/Mira --resolver-config /Users/angwei/Sandbox/Mira/data/v3/provider_resolvers.json --adapter-config /Users/angwei/Sandbox/Mira/data/v3/provider_adapters.json --write-env-template /Users/angwei/Sandbox/Mira/data/v3/provider_provisioning.tts.template --overwrite-env-template --skip-resolvers --require-adapter tts --json
```

Provision real `tts` endpoint/token values in the operator's secret-backed shell, launchd environment, or secret manager. Do not commit secrets into `/Users/angwei/Sandbox/Mira/data/v3/provider_provisioning.tts.template`.

After provisioning, check scoped readiness:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_readiness.py --root /Users/angwei/Sandbox/Mira --resolver-config /Users/angwei/Sandbox/Mira/data/v3/provider_resolvers.json --adapter-config /Users/angwei/Sandbox/Mira/data/v3/provider_adapters.json --skip-resolvers --require-adapter tts --json
```

After scoped readiness passes, preview the production canary without mutating state:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_production_canary.py --root /Users/angwei/Sandbox/Mira --resolver-config /Users/angwei/Sandbox/Mira/data/v3/provider_resolvers.json --adapter-config /Users/angwei/Sandbox/Mira/data/v3/provider_adapters.json --provider tts --dry-run --json
```

Only after scoped readiness reports ready, run the production canary:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_production_canary.py --root /Users/angwei/Sandbox/Mira --resolver-config /Users/angwei/Sandbox/Mira/data/v3/provider_resolvers.json --adapter-config /Users/angwei/Sandbox/Mira/data/v3/provider_adapters.json --provider tts --json
```

Keep any provider promotion behind approval, effect logging, reconciliation, and causal evidence.

## Open Operator Review: Effect Reconciliation

Standard: inspect unresolved side effects against replay-bundle and provider evidence before retrying, reconciling, or closing the effect.

Current first unresolved effect:

- effect id: `effectlog_fceafed9b77d`
- pipeline/action: `article_creation` / `publish_substack`
- target: `V3.1 staged publish effect smoke`
- status: `planned`
- idempotency key: `article_creation:publish_substack_idempotent:V3.1 staged publish effect smoke`
- preview hash: ``
- approval token id: ``
- replay bundle ref: `/Users/angwei/Sandbox/Mira/data/v3/effect_replay_bundles/article_creation_13f12c7963bd-publish_substack-1c717ac8649bedc4-recovered.json`
- external ref: ``
- reconciliation ref: ``

Inspect the effect without mutating the effect log:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_effect_reconciliation.py --effect-id effectlog_fceafed9b77d --json
```

When operator evidence lives outside the default provider-state directory, add `--publish-manifest <path>`, `--rss-feed <path>`, or `--provider-state-manifest <path>` to the inspector command before deciding whether reconciliation is justified.
Do not retry or mark the effect complete from local intent alone; reconcile only after provider evidence proves the external side effect succeeded, failed, or is still unknown.

## Verification After Operator Actions

Run these checks after recording feedback or provisioning providers:

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_status.py --actions
```

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_status.py --json
```

```bash
PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_north_star_report.py --output-dir data/v3/artifacts/north_star_reports --window-days 7
```

```bash
PYTHONPATH=lib .venv/bin/python -m pytest tests/v3 -q
```

The goal is not complete until the dashboard strategic scorecard no longer reports the watch gates above and the implementation matrix has no non-external or newly introduced failed checks.
