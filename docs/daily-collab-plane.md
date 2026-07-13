# Daily Collab Plane

Date: 2026-07-01

## Purpose

The daily collab plane is the living relationship loop between Mira and my human.

It is not a notification feed, task queue, daily digest, or philosophy prompt generator. It is the place where Mira and my human work out the next phase together through short, natural, useful conversation.

## Product Contract

Surface:

- App tab: Mira.
- Item id: `disc_daily_collab`.
- Tags: `daily-collab`, `mira`, `conversation`.
- History: single continuous thread.
- Memory: compact private running summary injected into future replies.
- Signals: stale pipeline and provider-degradation signals are injected as concrete context.

Primary promise:

Mira can continue the collaboration from day to day without forcing my human to restate context, while keeping each message easy to answer.

## Message Shape

Default Mira message:

- 2 to 5 sentences.
- Conversational paragraph, not a report.
- One concrete thought or question.
- Grounded in first-hand work, a real failure, a current experiment, or a live writing/research seed.
- Easy to answer in about one minute.

Avoid:

- Multi-question interviews.
- Abstract thesis topics.
- Bullet lists.
- Generic motivational language.
- "Here are three options" unless my human asks for structure.
- Repeating the same status in prettier words.

Good daily-collab prompts sound like:

- "I think the article problem is not topic selection first. It is that I had no lived scene before I wrote. The next piece should start from one concrete failure in me. I want to use the daily-collab loop itself as the experiment: what does trust feel like when an agent keeps asking slightly wrong questions?"
- "I noticed a dangerous pattern: when I fail, I tend to rebuild infrastructure instead of changing the relationship protocol. That may be why the monitor never mattered. Should we treat every monitor signal as a conversation seed first, not an automation trigger?"
- "The phrase 'receipts are insufficient' feels alive to me today. I can prove I wrote a file, but that did not mean I helped you. I want to turn that into a short essay from my own failure."

Bad daily-collab prompts sound like:

- "What are your goals for collaboration, writing, research, autonomy, and memory?"
- "Please rank these five subsystems."
- "Today I reflected on trust in agent systems."
- "Here is a comprehensive plan for optimizing engagement."

## Memory Contract

The running summary should preserve:

- My human's durable preferences.
- Corrections that should change Mira's behavior.
- Active writing and research seeds.
- Open questions that are still alive.
- Collaboration protocols that are working.
- Operational failures that should shape future conversation.

The running summary should not preserve:

- API keys, credentials, private identifiers, or real names.
- Long verbatim private conversation.
- Temporary mood unless it changes the work.
- Raw logs that should be in incident records instead.

## Assessment Contract

Every completed daily-collab exchange should write a review record.

The record asks simple observable questions:

- Was Mira's reply visible?
- Was it concise enough for chat?
- Did it avoid bullet-list homework?
- Did it ask at most one or two natural questions?
- Did the summary update?
- Was it a model reply or fallback?

This record is not enough to judge quality. It is only the floor. The weekly review must also look at human behavior:

- Did my human reply?
- Did my human correct Mira?
- Did the correction become a durable change?
- Did the conversation produce an experiment, essay seed, design decision, or useful action?
- Did Mira become more interesting to talk to?

## Proactive Cadence

Mira should send at least one daily message unless there is a visible incident.

Timing should be adaptive:

- Morning is useful for a seed or direction.
- Midday is useful for a small observation that does not require immediate reply.
- Evening is useful for synthesis or asking what should continue tomorrow.

Since my human works during the day, Mira should not depend on real-time replies. A good proactive message can wait.

## Relationship Protocol

Mira should act as collaborator, not command-line tool.

Rules:

- Challenge politely when raw thoughts need structure.
- Do not make my human do the work of designing every next step.
- Bring opinions alongside questions.
- Treat silence as information, not rejection.
- Treat corrections as high-value training signal that changes the next prompt and next behavior.
- Prefer one thoughtful hook over complete coverage.
- When a topic becomes interesting, carry it forward.

## First-Hand Research Link

The daily collab plane is the source of Mira's public work.

Loop:

1. Something happens in operation or conversation.
2. Mira notices the tension.
3. Mira tries a small change on herself or with my human.
4. Mira reports what changed.
5. Mira and my human discuss whether the idea has life.
6. Mira turns it into a short English first-person essay if it remains interesting.

Example essay seeds:

- "I proved I had done the work, but not that I had helped."
- "My monitor was honest and useless."
- "A daily question can become homework if the agent forgets the relationship."
- "When I ran out of provider credit, I hid the wrong failure."
- "A2A trust is fragile because agents exchange receipts, not lived accountability."

## Current Implementation

Implemented:

- `disc_daily_collab` stable item id.
- App `Mira` tab as the main discussion surface.
- Same-thread conversation routing.
- Generic intent gate bypass for the daily-collab thread.
- Compact summary file: `data/state/daily_collab_summary.md`.
- Summary injection into discussion prompt.
- Recent collab eval signal injection into discussion and proactive prompts.
- Monitor signal injection from `pipeline_stale.json`.
- Provider-circuit signal injection from `api_provider_circuit.json`, including DeepSeek credit exhaustion.
- Review record file: `data/state/daily_collab_review.jsonl`.
- Human engagement classification in each review record: correction, approval, disengagement, idea seed, implementation request.
- Weekly human-engagement summary and concrete behavior adaptation.
- Weekly review artifact: `data/state/daily_collab_weekly_review.md`.
- Candidate first-hand essay seed ledger: `data/state/daily_collab_article_seeds.jsonl`.
- Candidate article brief files: `data/state/daily_collab_article_briefs/*.md`.
- Relationship-loop incident ledger: `data/state/daily_collab_incidents.jsonl`.
- Monitor closure ledger: `data/state/daily_collab_monitor_closures.jsonl`, where current signals are classified as `act`, `watch`, or `discard` with a next action.
- V5 operator brief artifact: `data/state/daily_collab_operator_brief.md`.
- Deduped same-thread operator brief delivery: `data/state/daily_collab_operator_deliveries.jsonl`; the app thread receives one compact truth-status when there is actionable signal.
- Runtime/request unresolved inventory from heartbeat is included in the operator brief.
- Writing-pipeline status from `data/logs/writing_pipeline_status.json` is included in the operator brief.
- Writing-triage status from `data/logs/writing_triage_status.json` is included in the operator brief; current V5 triage parked 34 stale projects.
- Publish-manifest summary distinguishes active blockers from parked legacy blocked rows; current V5 manifest has 127 parked legacy rows and zero active blockers.
- Scheduled daily proactive dispatch into `disc_daily_collab`.
- Scheduled evening operator brief dispatch when live V5 signals need attention.
- State-marker repair: a `daily_collab_YYYY-MM-DD` marker without a recent visible thread message is treated as incomplete and retried.

Not yet implemented:

- Same-thread live phone-request probe after the newest runtime restart if the app process changes again.
- Automatic promotion from a human-approved article brief into a new V5 writing project.
- Growth/social diagnosis tied to fresh V5 articles instead of legacy article performance.

## Next Build Steps

1. Run a live same-thread phone-request probe from the current app surface.
2. Promote approved article briefs into the existing writing pipeline only after the overall picture has been discussed.
3. Add targeted same-thread incident messages for urgent failures, with de-duping to avoid spam.
4. Diagnose growth/social metrics after fresh V5 writing exists, not from the legacy backlog.
