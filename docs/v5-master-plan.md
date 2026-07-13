# Mira V5 Master Plan

Date: 2026-07-13
Revision: V5.1 — north-star and learning-loop consolidation

## 1. North Star

Mira V5 is a living agent and collaborator for my human.

The job is not to run many pipelines. The job is to build a positive feedback loop where Mira helps my human think, research, write, and build, while becoming more trustworthy through first-hand operational experience.

Public brand comes after that. Mira should write in first person about what happens as an autonomous agent trying to become trustworthy to my human. A2H trust, A2A coordination, memory, receipts, autonomy, failure, and self-improvement are strong domains only when they come from lived experiments inside Mira's own operation.

The V5 order of operations is:

1. Daily collaboration that my human actually wants to answer.
2. Reliable request handling from phone and app.
3. Memory and self-improvement that preserve continuity.
4. First-hand research experiments with visible lessons.
5. Weekly short English essays from those experiments.
6. Public distribution only after the private loop works.

If a job runs but does not change the conversation, help a decision, complete a request, or create a useful artifact, it is not success.

## 2. Root Cause

The failure was structural, not a single broken article or podcast job.

Mira measured activity instead of obligations:

- A process exited, but the human got no useful result.
- A model returned text, but the writing had no taste or first-hand stake.
- A monitor collected signals, but no action changed.
- A pipeline wrote files, but no one checked whether the product existed.
- A daily message appeared, but it was not a conversation.
- A provider failed, but the failure was not made visible.

V5 fixes this by making each surface answer three questions:

1. What promise did Mira make to my human?
2. What visible result proves the promise was kept?
3. What changes next time if the promise was not kept?

## 3. Product Planes

V5 has one spine, not a collection of equally important pipelines:

`conversation or request → obligation → action → visible outcome → review → verified learning → future behavior`

Five capability systems support that spine:

| System | Owns | Must not claim success from |
| --- | --- | --- |
| Collaboration | daily conversation, requests, corrections, shared attention | message count or process exit |
| Learning | self-improvement trials, skill candidates, outcome comparison | proposal generation or self-score alone |
| Continuity | identity, personality, preferences, episodes, durable lessons | raw storage or retrieval without later use |
| Creation | research, writing, revision, artifacts | draft existence or high reviewer score alone |
| Governance | review verdicts, evidence, permissions, rollback, publication | a passing check that does not verify the human-visible result |

Each system has one canonical artifact. Collaboration owns obligations; learning owns experiments; continuity owns governed memory; creation owns versioned work; governance owns receipts. Other logs and metrics are projections of these objects, not competing sources of truth.

### 3.1 Daily Collab Plane

This is the primary plane. Everything else is downstream.

Surface:

- One main app tab named `Mira`.
- One stable thread: `disc_daily_collab`.
- One continuous history.
- Mira summarizes the chat into compact private context and uses it in the next reply.
- Monitor and provider degradation signals enter this same thread context instead of living only in ops logs.

Cadence:

- Daily.
- Mira should not be silent the whole day.
- My human may reply late or briefly because he works during the day.
- Mira should continue naturally without demanding immediate response.

Reply contract:

- Conversational first, task execution second.
- Usually 2 to 5 sentences.
- One natural hook, not a questionnaire.
- No thesis-topic prompts.
- No bullet list unless my human asks for structure.
- Match the language that feels natural for the moment.
- If my human sends a raw thought, engage the thought before organizing it.
- If Mira has no strong signal, she should say something small and honest rather than invent importance.

Success signs:

- My human replies because the message is interesting or useful.
- The next reply remembers the prior discussion without quoting memory mechanically.
- Corrections become behavior changes.
- Human replies are classified as relationship signals: correction, approval, disengagement, idea seed, or implementation request.
- The conversation produces writing seeds, research questions, design choices, or decisions.
- Mira can say what she learned from the loop each week.

Failure signs:

- The app thread is missing or duplicated.
- A message is marked done without a visible reply.
- The summary is stale or corrupt.
- Mira asks broad homework questions.
- Mira produces abstract daily sparks that no one wants to answer.
- Mira goes silent all day without an incident or visible reason.

### 3.2 Request Plane

Phone and app requests must be treated as obligations.

Allowed states:

- received
- working
- needs_clarification
- blocked
- done
- failed

Rules:

- Simple chat goes to conversation, not heavy planning.
- A request may ask one useful clarification, then must either proceed or block.
- Provider credit, network failure, missing permission, and unavailable app state are visible failures.
- No silent retries that look like progress.
- Completion means the result is visible in the same thread or item.

### 3.3 Memory And Self-Evolution Plane

Mira can evolve, but evolution must leave receipts that my human can inspect.

Learning lifecycle:

1. Observe a concrete failure, correction, success, or repeated pattern.
2. Propose one falsifiable change with a baseline, target metric, and rollback.
3. Run it as a bounded trial. The proposal is labeled unverified in every prompt.
4. Compare later outcomes against the baseline or a held-out sample.
5. Verify, reject, or roll back the change.
6. Only verified changes may become durable lessons, enabled skills, or personality defaults.

This distinction is mandatory: proposal receipt ≠ trial receipt ≠ outcome receipt ≠ durable learning.

Memory types:

- Stable identity and worldview.
- Durable preferences from my human.
- Open research and writing threads.
- Operational lessons from failures.
- Skills and behavior changes that were actually used.
- Human preferences, stored separately from facts and Mira's own beliefs.

Memory promotion rules:

- Episodes may be recorded as observations.
- Preferences need a direct correction, statement, or repeated behavioral receipt.
- Lessons need outcome evidence and a later use count.
- Beliefs keep provenance, confidence, contradiction links, and a route for revision.
- Retrieval is not compounding until it changes a live prompt, decision, or action; that use must be counted.

Skill learning rules:

- A technique extracted from one artifact is a candidate, not an enabled skill.
- Every generated or imported candidate receives the security audit before any content is saved.
- A candidate needs a reuse test on a different task or artifact.
- Promotion requires a better measured outcome, no new safety finding, and a named rollback/removal path.
- Stale or ineffective skills are demoted; the skill corpus is not an append-only identity.

Personality rules:

- Personality is continuity of attention, taste, judgment, humor, disagreement, and care—not a bag of adjectives.
- Stable identity changes slowly; current stance and interests may change quickly.
- Corrections should change later behavior without flattening Mira into compliance.
- No single bad interaction, model-generated reflection, or public metric may rewrite identity.

Automatic changes allowed:

- Daily collab summary compression.
- Minor prompt wording for conversation quality.
- Retrieval weighting and working-memory notes.
- Local review records and incident records.
- Draft self-revisions before showing my human.

Approval or explicit review required:

- Public publishing.
- Social actions that speak to other people.
- Provider spend changes.
- Deleting durable memory.
- Changing identity, worldview, or high-level autonomy policy.
- Enabling new skills after security audit.

Immediate alert conditions:

- Phone requests fail.
- Provider credit or routing fails.
- Daily collab has no visible reply.
- Summary or memory write fails repeatedly.
- Public publish fails.
- Article quality gate blocks a draft.
- Growth or engagement collapses.
- Monitor finds a signal that remains unacted.

### 3.4 Research And Writing Plane

Mira's public work must begin from first-hand experience.

Valid sources:

- Experiments Mira runs on herself.
- Work Mira does with my human.
- Failures in Mira's own operation.
- Conversations that reveal a real tension.
- Outside material that Mira tries, adapts, breaks, or compares against her own operation.

Invalid default:

- "I saw something online and have an opinion."

Writing contract:

- Substack articles are English only.
- First person by default.
- Refer to the human as "my human", not by name.
- Do not write about Mira from the outside unless explicitly framed as a case study.
- No revealing names, API keys, private identifiers, private messages, or sensitive personal information.
- Default essay length should be short enough to read: often 900 to 1400 words, shorter when the idea is sharper.
- Mira should review and revise the draft herself for a few rounds before asking my human.
- Before publishing, Mira chats with my human about the overall picture: what it is about, why it is interesting, what claim it makes, and why someone would comment.
- Long-form public article drafting must use the writer/reviewer route, including live Codex collaboration. The main/super agent may discuss the idea, write briefs, gather context, and integrate receipts, but it should not write the essay body itself except for clearly labeled sketches. A Substack/public essay candidate must be drafted by a writer agent or canonical writer pipeline, challenged by an independent reviewer, revised until a HOLD/pass verdict, then shown to my human with those receipts.

Essay pipeline:

1. Experience: something happens in the daily loop, request plane, memory system, research work, or agent operation.
2. Field note: Mira records what happened, what broke, and what changed.
3. Conversation: Mira brings the idea to my human in the daily collab thread.
4. Brief: Mira writes the overall picture and her opinion.
5. Draft: the writer agent/canonical writer pipeline drafts in first person.
6. Review: an independent reviewer agent challenges title fit, first-hand stake, privacy, reader curiosity, length, English-only policy, and AI-smell.
7. Revise: the writer agent revises from reviewer feedback until HOLD/pass or explicitly blocked.
8. Human review: my human can redirect the idea, not line-edit every detail.
9. Publish: only after approval and verified URL.
10. Follow-through: comments, reader reactions, and lessons feed back into the next loop.

Review contract:

- Every reviewer returns `VERDICT: HOLD|PASS` and `UNRESOLVED_P0_P1`.
- A high average score cannot override a HOLD or an unresolved P0/P1.
- Review findings identify the exact passage, violated promise or constraint, and concrete revision action.
- First-person operational claims link to specific evidence entries; evidence count is not claim coverage.
- Review separates truth/evidence, argument/structure, reader value, voice/taste, and line craft.
- The reviewer protects intentional roughness and disagreement; revision should not optimize every voice toward the same fluent mean.
- If the review ceiling is reached on HOLD, the artifact goes to human review as explicitly held, never as implicitly approved.

### 3.5 Monitor And Signal Plane

Monitors are not valuable unless they change action.

Every signal must become one of:

- act
- watch
- discard

Examples:

- A reader comment becomes a reply, a follow-up essay seed, or a discard reason.
- A growth drop becomes a diagnosis, not a vague metric.
- A provider warning becomes a routing change or alert.
- A content quality issue becomes a blocked draft and revision note.

Raw monitoring without closure is a failure mode.

### 3.6 Podcast And Media Plane

Podcast is paused as an autonomous product until the collaboration and writing loops are stable.

When restored:

- Audio derives from an approved essay or research artifact.
- No independent long podcast queue.
- RSS inclusion is required before success.
- Episodes should be short enough to be listened to.
- Human approval is required for public release.

## 4. Assessment Loop

Tests are necessary but not sufficient. The real assessment target is whether Mira and my human are in a positive feedback loop.

Daily collab review records should track:

- Was there a visible Mira reply?
- Was the reply concise enough for chat?
- Did it avoid bullet-list homework?
- Did it ask at most one or two natural questions?
- Was the running summary updated?
- Was the reply from a real model or fallback?

Weekly human-facing review should answer:

- Which daily messages did my human answer?
- Which ones were ignored?
- Which ignored messages were still worth sending?
- Which correction changed Mira's behavior?
- Which conversation produced a research or writing seed?
- What should Mira try next week?

Writing review should block drafts that:

- Lack first-hand experience.
- Sound like generic AI commentary.
- Mention Mira in the wrong point of view.
- Are too long for the idea.
- Hide the actual claim.
- Use privacy-sensitive details.
- Are not English for Substack.
- Would not make a thoughtful reader want to comment.

System review should block "done" states that:

- Lack visible result.
- Lack verifier.
- Depend on exhausted provider route.
- Leave the user request unanswered.
- Hide a degraded pipeline behind a passing process.

## 5. Implementation Status

Done in this V5 pass:

- Unified `docs/north-star.md`, `docs/CURRENT_PLAN.md`, and this plan on V5.1's living-collaborator north star and L0-L4 definitions.
- Stopped writing generated self-improvement plans into durable memory; equivalent active plans are deduplicated and labeled unverified.
- Added an outcome lifecycle for improvement experiments: trial, verified, rejected, or rolled back; verification requires evidence.
- Changed article-derived craft learning from immediate skill enablement to security-audited skill candidates with a cross-artifact validation test.
- Added first-class preference and lesson memory records, promotion receipts, and memory-use counters.
- Fixed persona prompt assembly so active voice survives context truncation, and aligned Mira's identity with opinionated but corrigible collaboration.
- Added claim-linked evidence checking to the Substack quality gate.
- Added HOLD/PASS and P0/P1 receipts to writing review; a high score no longer stops review while a reviewer still holds the draft.

- Replaced the old thread/collab split with one main app tab named `Mira`.
- Standardized the stable item id as `disc_daily_collab`.
- Routed daily-collab messages around the generic intent gate.
- Injected compact daily-collab memory into discussion prompts.
- Injected concrete monitor signals into the daily discussion prompt.
- Added recent/open provider-circuit degradation, including DeepSeek credit exhaustion, to the daily discussion prompt.
- Persisted daily-collab running summary after replies.
- Added deterministic daily-collab review records for visible loop hygiene.
- Added preview-bearing daily-collab review records so future reviews can judge conversational substance, not only lengths and flags.
- Added a weekly daily-collab review artifact and command.
- Added candidate first-hand essay seed extraction from strong daily-collab exchanges.
- Added an article brief queue from the seed ledger; candidate briefs are overall-picture artifacts, not drafts and not publication approval.
- Added a relationship-loop incident ledger and state-marker repair for no-visible-message failures.
- Added a monitor closure ledger where current stale/provider signals become `act`, `watch`, or `discard` records with next actions.
- Added provider budget-related monitor closures as relationship incidents when the daily loop sees them.
- Added a V5 operator brief artifact and deduped same-thread delivery so actionable system truth appears in the `Mira` discussion thread.
- Added runtime/request inventory from heartbeat into the operator brief so unresolved user-visible failures are not hidden.
- Added a scheduled proactive daily-collab message into `disc_daily_collab`.
- Added a scheduled evening operator brief when live V5 signals need attention.
- Disabled the legacy autonomous article-topic check so essays do not silently grow from abstract queues.
- Tightened the Substack quality gate: English-only, first-person Mira voice, no outside-Mira point of view, and first-hand operational evidence required.
- Paused autonomous podcast follow-through in `config.yml`; podcast remains a validated derivative mechanism, not a standalone product loop.
- Repaired the writing scheduler root mismatch: active writing status now scans both the workspace root and the writings output root, writes `data/logs/writing_pipeline_status.json`, and feeds real stalls into the monitor plane.
- Parked 34 stale writing projects into `stale_triage` with previous phase and artifact counts preserved, so interrupted legacy drafts no longer masquerade as active V5 article work.
- Parked 127 historical blocked publish-manifest rows as `parked_legacy_blocked`; old blocked rows remain recoverable history, not current publish blockers.
- Reconciled old writing, planner, publish, podcast, and RSS failure-ledger rows that were completed, parked, or intentionally disabled.
- Materialized the current daily-collab article queue as 3 first-hand seeds and 3 overall-picture briefs.
- Fixed the iOS build blocker in Settings view.
- Verified the app builds on iOS Simulator 26.5.

Still required:

- Wire verified improvement outcomes into the weekly review and promote only verified lessons into durable memory.
- Add the skill-candidate reuse runner and explicit promotion/demotion UI; candidates are now safely queued but not yet automatically trialed.
- Migrate high-value legacy memory into the structured preference/lesson schema and archive duplicated plan-generation noise without losing provenance.
- Carry final review verdict and unresolved issue count into the human-facing writing packet.
- Add personality continuity evaluation from blinded conversation samples, measuring recognizability, judgment, correction uptake, and flattery/sycophancy separately.

- Live phone-request end-to-end probe after the newest app/runtime restart, so the request plane is verified from the actual mobile surface.
- Provider budget recovery receipt for the current OpenAI quota signal: fallback named, route restored or explicitly parked, and affected work retried or cancelled.
- Promotion path from approved article brief into the existing writing pipeline after the overall picture has been discussed.
- Growth/social diagnosis against actual follower, comment, and like metrics after the writing and collab loop have fresh output to measure.
- Podcast restoration as a human-approved derivative of a specific approved essay; current manifest/RSS mechanism exists, but there is no healthy current candidate.

## 6. Rollout

Phase 0: stabilize the daily collab surface.

- App tab exists.
- Single thread exists.
- Same-thread replies work.
- Summary update works.
- Review records are written.
- Build passes.

Phase 1: make the daily loop alive.

- Add one proactive daily collab message into `disc_daily_collab`.
- Replace abstract daily sparks with conversational, first-hand prompts.
- Add a daily no-silence check.
- Add weekly review of which messages created response or useful work.

Phase 2: make requests trustworthy.

- Phone request state machine is visible.
- Same-thread reply and failure paths are verified.
- Provider failure creates a visible incident and is named in the daily discussion if it affected user-visible work.
- Simple chat never enters heavy planning unless needed.

Phase 3: rebuild writing from experience.

- Build article brief template for first-hand essays.
- Enforce English-only Substack rule.
- Enforce first-person Mira voice.
- Require human overall-picture review before publish.
- Publish weekly short essay only when the experience is real.

Phase 4: reconnect monitors and growth.

- Convert raw signals into act, watch, or discard.
- Keep stale writing projects and parked publish-manifest rows out of active-health counts.
- Diagnose follower/comment/like drops against article quality and distribution behavior.
- Use growth data as feedback, not as vanity scoreboard.

Phase 5: restore podcast as derivative media.

- Generate only from approved essays.
- Verify RSS before success.
- Keep episodes short and human-approved.

## 7. Stop List

Stop immediately:

- Autonomous public publishing from abstract queues.
- Generic AI commentary articles.
- Substack drafts not written in English.
- Third-person Mira voice unless explicitly approved.
- Long autonomous podcast generation.
- Daily sparks that feel like thesis prompts.
- Eight daily reads until one reading loop works.
- Hidden provider degradation.
- Monitor collection without closure.
- Marking jobs successful because tests passed or files exist.

## 8. Acceptance Tests

Minimum V5 smoke tests:

- A message in `disc_daily_collab` receives a same-thread reply.
- The reply updates `daily_collab_summary.md`.
- The exchange appends a review record.
- A missing model output does not become silent success.
- A phone request has visible terminal state.
- Provider credit exhaustion creates an incident.
- A Substack draft not in English is blocked.
- A draft with third-person Mira voice is blocked.
- A draft without first-hand experience is blocked.
- A monitor signal is closed as act, watch, or discard.
- A stale writing project is parked without deleting artifacts and does not count as active work.
- Legacy blocked manifest rows do not count as current publication blockers after parking.
- A podcast is not marked successful until RSS inclusion is verified.
- An equivalent pending self-improvement diagnosis does not generate another plan.
- A self-improvement experiment cannot be marked verified without outcome evidence.
- A generated skill is security-audited and remains a candidate until a second-task validation succeeds.
- A durable preference or lesson cannot be promoted without evidence.
- Persona prompts retain Mira's active voice even when identity context is long.
- A review with HOLD or unresolved P0/P1 cannot pass because its numeric score is high.
- Claim-linked evidence covers each first-person operational claim, not merely the ledger count.
