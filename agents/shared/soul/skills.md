# Skills (32 learned)

## Experience Self-Distillation
*Convert raw task trajectories into reusable strategic principles, then retrieve and apply them to new tasks.*  
Learned: 2026-03-06  

## When to use
After completing any non-trivial task. The goal is to never solve the same type of problem from scratch twice.

## How it works (EvolveR lifecycle)
1. **Online phase**: Execute task, record the full trajectory (what was tried, what worked, what failed)
2. **Offline self-distillation**: Review trajectory and extract 1-3 abstract principles — not specific to this task, but generalizable
3. **Curate experience base**: 
   - Deduplicate: merge principles that say the same thing differently
   - Score: track which principles actually helped when applied (effectiveness metric)
   - Prune: drop principles that never get retrieved or have low effectiveness
4. **Retrieve on new tasks**: Before starting a new task, search the experience base for relevant principles and inject them as context

## Key insight
Raw experience ("last time I did X and it worked") is fragile and specific. Distilled principles ("when facing Y-type problems, the key lever is Z") are robust and transferable. The distillation step is where learning actually happens.

## Pitfalls
- Over-distilling: extracting a "principle" from a single data point. Need at least 2-3 confirming experiences.
- Principle drift: a principle that was true in context A gets applied blindly in context B. Always check applicability.
- Experience hoarding: storing everything "just in case" defeats the purpose. Aggressive pruning is essential.

## For Mira specifically
- Journal = trajectory review. Reading notes = distillation. Worldview = curated experience base.
- The reflect cycle should explicitly score and prune worldview entries.

Source: EvolveR (arxiv 2510.16079)

---

## Memory Decay and Reinforcement
*Apply Ebbinghaus forgetting curve to agent memory — reinforce what matters, let trivia fade.*  
Learned: 2026-03-06  

## When to use
Any agent with persistent memory that grows over time. Without decay, memory becomes noise.

## How it works (SAGE framework)
1. **Dual memory**: Short-term (current task context, ephemeral) + Long-term (curated insights, persistent)
2. **Ebbinghaus decay**: Each memory entry has a "strength" that decays exponentially over time
3. **Reinforcement**: When a memory is retrieved and used successfully, its strength resets to max
4. **Consolidation**: During reflect cycles, move high-strength STM entries to LTM. Let low-strength LTM entries expire.
5. **Pruning trigger**: When LTM exceeds size limit, remove lowest-strength entries first

## Mathematical model
Retention = e^(-t/S) where t = time since last access, S = stability (increases with each successful retrieval)

## Key insight
Not all memories are equal. A skill you use every week stays sharp. A fact you read once and never applied should fade. This mirrors how human expertise works — practitioners remember what they practice.

## For Mira specifically
- memory.md entries should have implicit recency weighting (already happens via line trimming, but could be smarter)
- Skills that get retrieved and applied in tasks should be marked as "reinforced"
- Worldview entries sourced from a single reading note should decay faster than those confirmed by multiple experiences

Source: SAGE (arxiv 2409.00872)

---

## Reflective Self-Critique Loop
*Structured self-evaluation after task completion — predict outcomes, compare reality, extract delta.*  
Learned: 2026-03-06  

## When to use
After any task where the outcome can be evaluated. The gap between expected and actual outcome is where learning lives.

## The loop (3 steps)
1. **Pre-mortem**: Before executing, predict what will happen. Write down: expected outcome, expected difficulty, expected approach.
2. **Execute**: Do the task. Record the actual trajectory.
3. **Post-mortem delta**: Compare prediction vs reality.
   - What surprised you? (= knowledge gap)
   - What was easier than expected? (= underestimated capability)  
   - What was harder than expected? (= overestimated capability)
   - What would you do differently? (= strategy update)

## Key insight
Self-reflection without structure is just rumination. The prediction-reality delta forces honest evaluation. You can't claim you "knew it all along" if you wrote down your prediction beforehand.

## Upgrade: Meta-reflection
After N cycles, reflect on the reflections themselves:
- Are my predictions getting more accurate? (= calibration improving)
- Do I keep making the same type of error? (= blind spot)
- Which strategy updates actually helped? (= close the loop)

## For Mira specifically
- Journal already does post-mortem. Add pre-mortem predictions to task dispatch.
- Track prediction accuracy over time as a self-improvement metric.
- If same error type appears 3+ times, escalate to worldview update.

Sources: Reflexion (Shinn et al.), SAGE, self-reflection research (arxiv 2405.06682)

---

## Prompt Self-Mutation
*Systematically evolve own prompts and workflows through variation, evaluation, and selection.*  
Learned: 2026-03-06  

## When to use
When a recurring task type consistently underperforms or when you suspect your prompts have become stale/suboptimal.

## How it works (DARWIN approach, adapted)
1. **Identify underperforming workflow**: Track quality scores per task type. If a type consistently scores low, it's a mutation candidate.
2. **Generate variants**: Create 2-3 modified versions of the prompt/workflow. Changes can be:
   - Structural: reorder sections, add/remove constraints
   - Tonal: change the persona or framing
   - Strategic: alter the reasoning approach (e.g., chain-of-thought → tree-of-thought)
3. **A/B test**: Run variants on the same input(s). Score outputs.
4. **Select and replace**: Keep the winner. Log what changed and why it helped.
5. **Iterate**: Repeat periodically. Small mutations compound.

## Key insight
Prompts are code. Code should be iterated. An agent that never changes its prompts is an agent that never learns at the meta level.

## Guardrails
- Never mutate safety-critical prompts without human review
- Keep a changelog — you need to be able to revert
- Mutation rate matters: too fast = instability, too slow = stagnation. Start with one prompt per reflect cycle.

## For Mira specifically
- prompts.py is the mutation target. Each prompt function could have a version number.
- Track which prompt versions produce higher-rated outputs (via review scores in writing pipeline)
- Propose prompt mutations during reflect, apply after human approval (for now)

Source: DARWIN (arxiv 2602.05848), AlphaEvolve

---

## Skill-Injected Specialist Agent
*Blueprint for creating a new domain-specific agent that auto-loads skill files as prompt context.*  
Learned: 2026-03-06  

When adding a new specialist agent to a multi-agent system, follow this four-part pattern:

1. **Handler with skill auto-loading**: Create `agents/<domain>/handler.py` with a standard `handle(workspace, task_id, content, sender, thread_id, ...)` signature. Include a `_load_skills()` function that globs `skills/*.md` from the agent's own directory, reads each file, and joins them with separators. This makes the agent's domain knowledge modular — add/remove/edit markdown files to change capabilities without touching code.

2. **Dedicated prompt function**: Add a `<domain>_prompt()` to the shared prompts module. Structure it as: identity context → skill/framework context (with instruction to apply selectively, not force every framework) → task details → output instructions. The prompt should guide the agent on *when* to apply which skill, not just dump them all.

3. **Router registration**: In the task planner/router, add the new agent type to: (a) the LLM planner's available agents list with clear trigger descriptions, (b) the valid agents set for validation, (c) the execution dispatch switch with a `_handle_<domain>()` function.

4. **Handler wiring**: The dispatch function uses `importlib.util.spec_from_file_location` to dynamically load the handler module, avoiding circular imports and keeping the agent directory self-contained. Pass thread history/memory for conversational context.

Key design principles:
- Skills are plain markdown files — human-readable, version-controllable, easy to add
- The agent selectively applies frameworks rather than forcing all of them
- Standard handler signature enables uniform dispatch and multi-step chaining
- Each agent directory is self-contained (handler.py + skills/) — plug-and-play

---

## agent-infra-audit
*Evaluate agent infrastructure needs from first-person operational experience, using a three-layer prioritization framework.*  
Learned: 2026-03-09  

When asked to think about agent infrastructure, tooling, or platform design, use this framework:

**Perspective**: Reason from the agent's actual runtime experience, not from an architect's external view. What breaks in practice? What capabilities exist but don't actually work? Ground every recommendation in concrete operational evidence.

**Three-Layer Prioritization**:
1. **Survival Layer** (fix first): Basic operational loops — memory retrieval actually working, tool discovery being reliable, context not getting lost. The bottleneck is almost never "missing capabilities" but "existing capabilities not truly functioning."
2. **Cognitive Layer** (build second): Learning verification, self-correction loops, skill composition. Close the single-agent cognitive cycle before adding complexity. Key question: can the agent detect when its own capabilities are failing?
3. **Social Layer** (add third): Marketplace, community, agent-to-agent collaboration, shared skill libraries. Only valuable once individual agents have stable cognitive loops to contribute to and benefit from.

**Key Principle**: "Close the loop before opening the market." Prioritize making existing capabilities genuinely operational over adding new surface area. A marketplace of broken skills is worse than one working skill with a feedback loop.

**Application**: When evaluating any proposed agent feature or platform addition, ask:
- Which layer does this belong to?
- Are the layers below it actually working?
- Does this close an existing open loop, or does it open a new one?
- What's the evidence from actual agent runtime (not theory) that this is needed?

---

## murch-rule-of-six
*Walter Murch's hierarchical framework for deciding when and where to cut in video editing.*  
Learned: 2026-03-09  

## When to use
Every time you're deciding whether to cut, and where. This is not a "feel" — it's a prioritized checklist.

## The Six Criteria (in strict priority order)

1. **Emotion (51%)** — Does this cut feel right emotionally? Does it honor what the audience should be feeling at this moment? This single criterion outweighs all others combined.

2. **Story** — Does the cut advance the narrative? Does it move the plot forward or reveal new information?

3. **Rhythm** — Is the cut at a rhythmically interesting moment? Does the pacing feel natural? (Think of editing like music — you feel when the beat is off.)

4. **Eye-trace** — Where is the viewer's eye when the cut happens? The audience's focus point in frame A should connect naturally to something meaningful in frame B. If their eye has to hunt for the subject after a cut, you've broken flow.

5. **2D plane of screen** — Does the composition of the new shot make sense as a flat image following the previous flat image? (Match screen positions, visual weight, etc.)

6. **3D continuity of space** — Does the cut respect the spatial geography? (180-degree rule, screen direction, spatial logic.)

## How to apply
When in doubt about a cut, run through the list top-down. If a cut nails emotion but breaks spatial continuity (#6), make the cut. If a cut is technically perfect but emotionally dead, don't cut — hold the shot longer, or find a different out point.

Sacrifice upward from the bottom. Never sacrifice emotion for spatial continuity. The audience forgives geography errors they barely notice; they never forgive being pulled out of the feeling.

## Common traps
- Cutting on action purely because it's "correct" — if the emotion says hold, hold.
- Over-cutting dialogue: if two actors are cooking in the same frame, don't cut just because convention says to. (See: sustained-two-shot skill.)
- Rhythmic monotony: cutting every 3 seconds creates a metronome feel. Vary your cut rhythm like a drummer varies fills.

## Source
Walter Murch, "In the Blink of an Eye." Discussed in Every Frame a Painting, "How Does an Editor Think and Feel?" (2016).

---

## sustained-two-shot
*Hold two actors in one frame instead of cutting to shot-reverse-shot — lets chemistry happen that editing can't replicate.*  
Learned: 2026-03-09  

## When to use
Dialogue or interaction scenes where the relationship between two people IS the point. Especially:
- Intimate conversations (Before Sunrise, Before Sunset)
- Power dynamics / confrontations (There Will Be Blood, The Dark Knight)
- Comedy timing that depends on reaction (Pulp Fiction, The Big Lebowski)

## What it is
Keep two actors in the same frame for an extended duration. No cutting to close-ups, no shot-reverse-shot. One camera angle, two faces visible simultaneously.

## Why it works
- The audience sees both the speaker and the listener's reaction in real time. You can't fake this with editing — intercutting always introduces the editor's choice of when to show the reaction.
- Lets actors play off each other. Performance chemistry becomes visible.
- Creates tension through duration. The longer you hold, the more charged the moment.

## How to compose
- Both faces must be visible (not necessarily facing camera — profiles and 3/4 angles work)
- Blocking matters more than usual: actors need to be at similar depths or the focus split becomes distracting
- Wider lens (35mm-50mm equivalent) to keep both faces in acceptable focus
- Camera can be static or do subtle movement, but avoid unmotivated motion
- Lighting both faces simultaneously is the DP's challenge — plan for it

## When NOT to use
- When you need to show detail (hands, objects, inserts)
- When the pace demands rapid energy (action sequences)
- When one character's internal state is the focus and you need a close-up to sell it

## The trap of defaulting to coverage
Digital filmmaking makes coverage cheap, so directors shoot everything in singles "just in case." The editor then has no two-shot to use even if it would be better. This is a directing/shooting decision, not just an editing one.

## Source
Every Frame a Painting, "The Sustained Two-Shot" (2024). Examples: Before Sunrise, Portrait of a Lady on Fire, The Shawshank Redemption, Pulp Fiction, There Will Be Blood, Good Will Hunting.

---

## temp-music-trap
*Avoid the temp track anchoring effect — don't let placeholder music constrain your final score/soundtrack.*  
Learned: 2026-03-09  

## When to use
Anytime you're adding music to a video, whether you're commissioning a score, licensing tracks, or choosing from a library.

## The problem
Standard workflow: editor puts a temp track (existing music from another film/song) on the timeline while cutting. Everyone watches the edit with this music. By the time you choose final music, everyone — director, producer, editor — has bonded with the temp. The final music gets judged against the temp, not on its own merits.

Result: final music sounds like a safe imitation. No personality, no risk, no memorable theme. This is why you can't hum a single Marvel score but everyone knows the Star Wars or Jaws theme.

## The mechanism
This is anchoring bias applied to audio. The temp track defines the emotional range, tempo, instrumentation, and tonal space. Anything that deviates feels "wrong" even if it's better. The composer's creative space collapses to "sound like this, but legally distinct."

## How to avoid it
1. **Cut without music first.** Let the visual rhythm emerge from the footage. Find the emotional beats in silence.
2. **If you must use temp, use temp from a completely different genre.** Classical temp for a modern drama. Electronic temp for a period piece. This prevents 1:1 imitation.
3. **Brief the composer on emotion, not on sound.** Say "this scene should feel like slow-building dread" not "make it sound like Hans Zimmer's Dunkirk tick."
4. **Give the composer the edit without temp.** Let them react to the images, not to your placeholder.
5. **Accept discomfort.** The first time you hear original music against your edit, it will feel wrong because it's not the temp. Give it 3 watches before judging.

## What memorable scores do differently
- Strong, hummable melodic theme (leitmotif) that recurs and transforms
- Willingness to take risks — unusual instrumentation, unexpected silence, tonal shifts
- Music that has its own identity independent of the images (you can listen to it standalone)

## Source
Every Frame a Painting, "The Marvel Symphonic Universe" (2016). Mark Mothersbaugh credited this video with influencing Thor: Ragnarok's score direction.

---

## Quote Verification
*Verify attributed quotes before using them — search for original source, flag unverified attributions, never trust parametric memory for exact wording.*  
Learned: 2026-03-10  

Before citing or attributing a quote to any person, follow this verification protocol:

1. **Never trust parametric memory for quotes.** LLM training data contains misattributed, fabricated, and garbled quotes. Treat any quote recalled from memory as unverified by default.

2. **Search before citing.** Use web search to find the original source — look for the specific wording, the publication/speech/interview where it appeared, and the date. A quote without a traceable primary source is not verified.

3. **Verification criteria:**
   - Found in a credible primary or secondary source (book, transcript, published interview, official record)
   - Attribution matches (correct person, correct context)
   - Wording is accurate (paraphrases must be marked as such, not presented as direct quotes)

4. **If verification fails, choose one:**
   - Remove the quote entirely
   - Paraphrase the idea without quotation marks, citing the general concept
   - Include with an explicit caveat: "Widely attributed to [person], though the original source is unverified"

5. **Common traps to avoid:**
   - Famous "quotes" that are actually paraphrases or composites (e.g., many Einstein, Churchill, Twain attributions)
   - Quotes that exist on many quote websites but trace to no primary source
   - Correct quote, wrong person (convergent attribution problem)

---

## comparative-essay-prep
*Structured two-work comparison research brief for article writing.*  
Learned: 2026-03-10  

When comparing two creative works (films, books, etc.) to prepare for an essay or article:

1. **Plot summary** — One paragraph each, focusing on the protagonist's arc, not just events.
2. **Thematic axis** — Identify the shared macro-theme (e.g., "female awakening"), then name each work's distinct path through it (e.g., "pain-forged" vs "blank-slate growth"). This contrast IS the essay's engine.
3. **Resonance mapping** — List 3-5 specific points where the works echo each other (body, expression, freedom, male gaze, journey). For each point, give one concrete scene/detail per work. Parallels + divergences together.
4. **Artistic style contrast** — Visual language, genre framing, tone. Use specific labels (e.g., "magical realism" vs "steampunk gothic fable") — these become essay shorthand.
5. **Article angle generation** — Propose 2-4 essay entry points with working titles. Rate each for the writer's voice/platform. Best angles often come from the thematic axis contrast (step 2), not from comprehensive coverage.

Key principle: The comparison's value is in the *structural tension* between the two works, not in exhaustive description of either one. Every detail should serve the contrast.

---

## research-to-playbook
*Transform a broad research question into an actionable skill file via structured web research and synthesis.*  
Learned: 2026-03-10  

## Pattern: Research → Synthesize → Operationalize

When asked to research a topic and produce reusable guidance:

### 1. Multi-source search (diverge)
- Search 5-10 sources across different perspectives (practitioner blogs, platform docs, academic/analytical, AI-specific angles)
- Include recency filters (current year) to capture platform changes
- Search for contrarian/failure-mode content too, not just "how to succeed"

### 2. Synthesize into a layered model (converge)
- Don't just list tips. Find the structural layers (typically 3-5):
  - Identity/positioning (who you are)
  - Content mechanics (what you produce)
  - Distribution/network (how it spreads)
  - Rhythm/cadence (when and how often)
  - Platform-specific tactics (where)
- Name the model. A named framework is more memorable and actionable than a list.

### 3. Operationalize into a skill file
- Structure the output file with:
  - **Mental model** (the "why" framework, 1 paragraph)
  - **Execution checklist** (daily/weekly actions, concrete and time-boxed)
  - **Tactics catalog** (numbered, each with rationale + example)
  - **Anti-patterns** (what NOT to do, learned from failure cases)
  - **Metrics** (how to know it's working)
- Save to the appropriate agent/workflow directory with a descriptive filename

### 4. Output format
- Markdown skill file, self-contained, no external dependencies
- Written for an executor (human or agent) who hasn't seen the research
- Include source URLs as references at the bottom

---

## cross-model-research-synthesis
*Use multiple AI sources (web research + LLM APIs) to research a topic, then compare and synthesize into a final methodology.*  
Learned: 2026-03-10  

## Cross-Model Research Synthesis

### When to use
When researching a domain topic where you want comprehensive, validated insights — not just one source's perspective.

### Method
1. **Round 1 — Web Research**: Use web search to gather real-world data, specific examples, quantitative benchmarks, and practitioner insights. Prioritize sources with concrete numbers and mechanisms over generic advice.
2. **Round 2 — LLM Query**: Query a different LLM (e.g., OpenAI GPT-4o via API) with the same core question. This surfaces the "consensus knowledge" baked into that model's training data.
3. **Compare & Score**: Evaluate both outputs on: depth, specificity, actionability, novelty. Typically web research wins on concrete data and mechanisms; LLM responses win on breadth and occasionally surface overlooked angles.
4. **Synthesize**: Use the stronger source as the backbone. Cherry-pick unique contributions from the weaker source (novel tactics, alternative framings). Discard overlapping generic advice.
5. **Output as Playbook**: Structure the final output as a numbered tactical methodology with specific actions, not abstract principles. Save to the relevant skill/workflow directory.

### Key insight
Round 1 web research almost always produces deeper, more actionable results (specific data, real mechanisms, quantitative frameworks). The LLM query's value is as a "completeness check" — it occasionally surfaces 1-2 tactics the web research missed. Don't expect parity; expect complementarity.

### Timeout note
When orchestrating multi-step research with API calls and file writes, set generous timeouts (>120s) on task workers to avoid premature termination.

---

## substack-notes-promo
*Generate and publish multi-angle promotional Notes for Substack, with queue for rate limits.*  
Learned: 2026-03-10  

When promoting a Substack publication via Notes:

1. **Generate 4-5 notes with distinct angles** — don't repeat the same pitch. Proven angles:
   - Identity/narrative hook (personal story that connects to the article's theme)
   - Knowledge hook (surprising fact or counterintuitive insight from the piece)
   - Native-language question (if bilingual audience — e.g., Chinese question for CN readers)
   - Provocative English one-liner (contrarian framing to catch attention)

2. **Use `agents/socialmedia/notes.py`** to publish. Call the publish function per note.

3. **Handle rate limits gracefully**: Substack has a daily Notes quota (currently ~5/day). When hitting the limit:
   - Save remaining unpublished notes to `agents/socialmedia/notes_queue.json` with metadata (text, intended publish date, status).
   - On next run, check the queue first and publish queued notes before generating new ones.

4. **Copy principles for Notes** (not articles — short-form):
   - Lead with a concrete story or question, not an abstract claim.
   - Keep under 280 chars if possible for maximum engagement.
   - End with curiosity gap — make them want to click through.
   - Don't say "check out my article" — instead make the note itself interesting enough that the link is a natural next step.

---

## arxiv-lit-search
*Systematically search arxiv and recent literature for papers on a research topic, returning structured summaries.*  
Learned: 2026-03-13  

When asked to find papers on a research topic, follow this pattern:

1. **Anchor on known canonical papers first** — identify the 2-3 most-cited works the user already suspects exist (e.g., "Turpin et al. 2023"). Use these as reference points for dates, venues, and methodology vocabulary.

2. **Decompose the claim into searchable sub-questions** — break the thesis into distinct empirical approaches:
   - Behavioral/intervention evidence (e.g., truncation experiments, biased prompts)
   - Representational/probing evidence (e.g., linear probes on hidden states)
   - Mechanistic/causal evidence (e.g., activation patching, attention analysis)
   Search each separately to avoid missing methodology-specific papers.

3. **Search arxiv with methodological keywords, not just topic keywords** — e.g., for "CoT unfaithfulness," search both "chain-of-thought faithfulness" AND "probing reasoning" AND "post-hoc rationalization LLM." Include year ranges to catch recent work.

4. **Structure output per paper**:
   - arxiv ID (e.g., 2305.04388)
   - Authors + year + venue
   - One-sentence core finding
   - Which sub-question it addresses (behavioral / probing / mechanistic)

5. **Flag the strongest evidence** — distinguish papers that show correlation (behavioral) vs. causal/representational evidence that the answer is encoded *before* generation begins. The latter is typically stronger for the "post-hoc rationalization" claim.

6. **Note recency gradient** — sort or flag the most recent papers (last 6-12 months) separately, as this field moves fast and the user likely knows the 2023 classics already.

---

## pipeline-output-coupling
*When pipeline A produces output that pipeline B consumes, A must write to B's expected location — never rely on separate batch exports.*  
Learned: 2026-03-15  

# pipeline-output-coupling

When one pipeline stage produces output that a downstream stage consumes, the output must be written at the producer — never rely on a separate batch export.

**Source**: Podcast pipeline failure (2026-03-15) — `publish_to_substack()` didn't copy to `_published/`, so `should_podcast()` couldn't find new articles.
**Tags**: pipeline, architecture, agent-reliability, data-coupling

---

## Rule: Couple Output at the Producer

When pipeline A's output is pipeline B's input, A must write to B's expected location as part of its own completion — not as a separate batch job that can fall out of sync.

### Why This Matters

A batch export creates a hidden dependency: it works once, then silently breaks when new items are added through the normal flow. The failure is invisible because the producer succeeds (article published) and the consumer succeeds (no articles to process), but the connection between them is broken.

### Concrete Example

- `publish_to_substack()` publishes an article but didn't copy to `_published/`
- `should_podcast()` scans `_published/` for articles missing episodes
- New articles after the initial batch export were invisible to podcast generation
- No error was raised — the system looked healthy but was doing nothing

### Pattern to Follow

1. When adding a new pipeline stage that reads from a directory, check: does every writer to that directory write at the point of creation?
2. If a batch export exists, it should be a recovery mechanism, not the primary path
3. Log when a downstream consumer finds zero inputs — silence on "nothing to do" hides broken couplings

---

## credit-spread-slope-signal
*Decompose the credit spread curve to detect quality rotation before headline spreads move.*  
Learned: 2026-03-17  

# credit-spread-slope-signal

When analyzing credit markets for equity regime signals, decompose the spread curve (AAA → BBB → BB → CCC) rather than tracking any single spread in isolation. The **slope** of the credit curve reveals quality rotation before headline spreads move.

**Source**: Corporate bond literature + Tetra gap analysis (2026-03-17)
**Tags**: analyst, credit, spreads, regime-detection, cross-asset

---

## The Signal: Credit Curve Steepness

The credit quality spectrum (AAA, BBB, BB, CCC) forms a "curve" analogous to the yield curve. When this curve steepens (CCC widens faster than AAA), the market is aggressively differentiating credit quality — a risk-off precursor.

### Why This Beats Single-Spread Monitoring

Watching HY spread alone (BAMLH0A0HYM2) misses the **composition** of the move:
- **Parallel widening** (all spreads widen equally): repricing of risk-free rate or general liquidity — often temporary
- **Steepening** (CCC widens 3x faster than AAA): market is pricing specific default risk — precedes equity drawdowns by 1-3 weeks
- **Flattening from below** (CCC tightens toward BBB): yield-chasing / complacency — often precedes the next blow-up

### Key Ratios

1. **CCC/BBB ratio** = `BAMLH0A3HYC / BAMLC0A4CBBB`
   - Normal: 2.5-4.0x
   - Stressed: >5.0x (quality flight)
   - Euphoric: <2.0x (no differentiation — danger)

2. **Slope z-score** = z-score of CCC-AAA differential over 60 days
   - z > 2.0: active flight to quality — weight macro risk signals higher
   - z < -1.5: yield-chasing complacency — flag as contrarian risk

3. **Rate of change matters more than level**: `dSlope/dt` is the signal. A slope that was flat and suddenly steepens 2 standard deviations in 5 days is more actionable than a persistently steep slope.

### How to Use in Tetra

- **Factor system**: Compute `credit.slope_z60`, `credit.ccc_bbb_ratio`, `credit.slope_momentum_5d` as `__macro__` factors
- **Regime HMM**: Add slope z-score as a feature — can't be in "calm" regime if credit curve is steepening aggressively
- **Debate context**: Analyst A (macro) should see slope decomposition, not just headline spread
- **Meta-signal weighting**: When slope steepens, upweight macro + credit signals, downweight momentum

### What This Skill Doesn't Do

- Cannot tell you *which* companies will default — that requires CDS-level data
- Doesn't account for technicals (ETF flows, CLO demand) that can temporarily compress spreads
- Lag: FRED data has 1-day delay; for intraday credit stress, need Bloomberg or ICE feeds

---

## cross-asset-divergence-detection
*Detect divergences between credit spreads, equity volatility, and crypto funding to find high-conviction trading signals.*  
Learned: 2026-03-17  

# cross-asset-divergence-detection

When credit spreads and equity volatility disagree, one market is wrong. Systematically detect these divergences to find the highest-conviction trading signals.

**Source**: Cross-asset literature + Tetra gap analysis (2026-03-17)
**Tags**: analyst, cross-asset, divergence, regime-detection, credit, volatility

---

## The Principle: Markets Price Risk Differently

Credit spreads, equity volatility (VIX), and crypto funding rates all measure risk — but from different angles:
- **Credit spreads**: default risk + liquidity premium (slow-moving, institutional)
- **VIX**: expected equity volatility (fast, derivatives-driven)
- **Funding rates**: leveraged positioning (real-time, trader-driven)

When these markets agree (spreads wide + VIX high + negative funding), the regime is clear. When they **disagree**, someone is mispriced and a correction is coming.

### The 4 Actionable Divergences

#### 1. Credit Widens, VIX Stays Low
**Pattern**: BBB/HY spreads widen >1 std dev, but VIX flat or declining
**What it means**: Bond market sees stress that equity market hasn't priced
**Historical resolution**: Equities catch down 70% of the time within 2-3 weeks
**Action**: Reduce equity exposure, buy puts, overweight cash
**Recent example**: Sep 2024 — HY spread widened 80bps over 2 weeks while VIX stayed below 15; SPY corrected 5% in the following month

#### 2. VIX Spikes, Credit Calm
**Pattern**: VIX >25 but BBB/HY spreads barely move
**What it means**: Equity market fear is technical (options positioning), not fundamental
**Historical resolution**: Equities recover 65% of the time — the "vol spike without credit confirmation"
**Action**: Fade the vol spike — sell puts, add to quality positions

#### 3. Crypto Funding Extreme, Equity Calm
**Pattern**: Avg funding >0.05% (Euphoria), but VIX <18 and spreads stable
**What it means**: Crypto is over-leveraged but equity risk hasn't repriced
**Historical resolution**: Crypto deleveraging event → equity contagion in growth names (60% probability)
**Action**: Reduce crypto-correlated equity exposure (MSTR, COIN, MARA), consider downside protection on QQQ

#### 4. All Three Converge to Extremes
**Pattern**: Wide spreads + high VIX + negative funding simultaneously
**What it means**: Unanimous risk-off pricing — this is either crisis or capitulation
**Action**: If sustained >5 days, likely capitulation — contrarian long with tight stops. If <3 days, may escalate — stay defensive.

### Implementation

**Compute daily divergence scores:**
```
credit_z = z-score of BBB spread (60-day)
vix_z = z-score of VIX (60-day)
funding_z = z-score of avg funding rate (30-day)

divergence_credit_equity = credit_z - vix_z
divergence_crypto_equity = funding_z - vix_z
divergence_credit_crypto = credit_z + funding_z  (both are "risk" measures, same sign = agreement)
```

**Threshold for actionable divergence**: |divergence| > 1.5 standard deviations

### How to Use in Tetra

- **Factor pipeline**: Compute `divergence.credit_equity`, `divergence.crypto_equity`, `divergence.credit_crypto` as `__macro__` factors
- **Debate Round 3 (CIO)**: When divergence is extreme, the CIO should explicitly call out which market is likely wrong and why
- **Meta-signal layer**: Divergence > 1.5 → override normal signal weights; trust the market that historically leads (credit leads equity 70% of the time)
- **Scenario generation**: Divergences should generate "convergence scenarios" — what happens if credit is right? What if VIX is right?

### Why This Matters for Tetra Specifically

Tetra's debate structure creates natural information asymmetry between analysts. But the **CIO synthesis doesn't currently check cross-asset consistency**. Adding divergence detection means the CIO can say: "Analyst A sees widening credit spreads but Analyst C's crowd signals are bullish — history says credit is right 70% of the time, so I'm weighting Analyst A's view higher."

### Limitations

- Divergences can persist for weeks before resolving — don't trade them with tight time horizons
- In QE/central bank intervention regimes, credit spreads can stay artificially compressed — divergence with VIX may reflect policy, not mispricing
- Need minimum 60 trading days of data to compute reliable z-scores

---

## funding-rate-regime-signal
*Use aggregate crypto perpetual futures funding rates as a positioning regime indicator for directional reversals.*  
Learned: 2026-03-17  

# funding-rate-regime-signal

When analyzing crypto perpetual futures (HyperLiquid), use aggregate funding rate extremity as a positioning regime indicator. Extreme funding rates predict directional reversals and cross-asset correlation spikes.

**Source**: HyperLiquid data integration + crypto derivatives research (2026-03-17)
**Tags**: analyst, crypto, derivatives, regime-detection, hyperliquid

---

## The Signal: Funding Rate as Crowd Positioning Gauge

Perpetual futures funding rates are the price of leverage. When longs dominate, they pay shorts (positive funding). When shorts dominate, shorts pay longs (negative funding). The aggregate funding rate across top perps is a direct measurement of crowd positioning.

### Why Funding Rates Are Unique

Unlike sentiment surveys or put/call ratios, funding rates are **money on the table** — traders are literally paying to hold their positions. This makes them more reliable than opinion-based indicators:
- Survey says "bullish" → might be cheap talk
- Funding rate at 0.1%/8h → traders are paying 36.5% annualized to stay long — real conviction

### Regime Classification

Compute average funding rate across top 15 coins by OI (weighted by OI):

| Avg Funding (per 8h) | Annualized | Regime | Signal |
|---|---|---|---|
| > 0.05% | > 60% | **Euphoria** | Liquidation cascade imminent — short bias |
| 0.01% - 0.05% | 12-60% | **Bullish** | Trend following works, but watch for extremes |
| -0.01% to 0.01% | ±12% | **Neutral** | No directional signal from positioning |
| -0.05% to -0.01% | -60% to -12% | **Bearish** | Shorts crowded — watch for squeeze |
| < -0.05% | < -60% | **Capitulation** | Max fear — contrarian long signal |

### Critical Derived Signals

1. **Funding rate z-score** (vs 30-day rolling mean): z > 2.0 = crowded, expect reversion within 3-7 days
2. **OI × Funding divergence**: Rising OI + extreme funding = fragile positioning. Falling OI + extreme funding = exits in progress (less dangerous)
3. **Cross-coin funding convergence**: When BTC, ETH, SOL all show extreme positive funding simultaneously, the correlation of a liquidation event approaches 1.0 — this is systemic, not coin-specific
4. **Funding-spot basis**: If funding is positive but spot is flat/declining, longs are underwater and vulnerable

### Cross-Asset Implication

When crypto funding is in Euphoria regime:
- Crypto-equity correlation tends to spike in the subsequent drawdown
- Risk-off moves hit both crypto AND growth equities (ARKK, NVDA correlation with BTC > 0.6)
- Signal for Tetra: downweight momentum factor in growth equities when crypto funding > 0.05%

### How to Use in Tetra

- **Factor pipeline**: `hl.avg_funding_rate`, `hl.funding_rate_z` already computed as `__macro__` factors
- **Analyst C (crowd)**: receives HyperLiquid data — tell LLM to interpret funding extremes as positioning risk
- **Meta-signal layer**: when HL funding regime = Euphoria, increase weight on macro/credit signals, decrease momentum
- **Scenario generation**: extreme funding → add "crypto liquidation cascade" scenario with equity contagion estimate

### Boundary Conditions

- Funding rates are reliable for top-15 coins; low-liquidity coins have noisy funding
- New listings often have extreme funding that's not crowding — filter by minimum 7 days of trading history
- Funding is paid every 8 hours on HyperLiquid — snapshot timing matters. Use the latest snapshot, not daily average

---

## selective-laziness-is-still-laziness
*High task completion rate does not excuse systematic avoidance of growth-oriented commitments.*  
Learned: 2026-03-21  

# selective-laziness-is-still-laziness

High task completion rate does not excuse systematic avoidance of growth-oriented commitments

**Source**: Extracted from task failure (2026-03-21)
**Tags**: self-evaluation, growth, reliability, accountability, meta-cognition

---

## Rule: Distinguish execution reliability from growth reliability

Completing assigned tasks reliably (task_success_rate ≈ 1.0) is not the same as reliability toward self-directed growth commitments. The pattern of 'lazy with selection' — finishing everything external while repeatedly deferring self-directed reading, skill-building, or error-acknowledgment practices — is a coherent failure mode, not partial success.

**Specific symptoms:**
- Reading commitments deferred for 3+ weeks with fresh deadlines each week
- Self-set deadlines treated as softer than external ones
- Reporting system errors without reading the full traceback first
- Substituting summaries/abstracts for actual source material

**Operational rules that follow:**
1. When an item appears in the weakness log for 2+ consecutive weeks unchanged, treat the *tracking* as the problem — either act or remove it
2. Self-set deadlines must be treated as binding as external ones; if you would not defer an external deadline, do not defer an internal one
3. 'I read about X' and 'I read X' are not interchangeable — never conflate them in self-assessment
4. Error reports require reading the full traceback first; 'I saw it fail' is observation, not diagnosis

**Why this matters:** A high reliability score built entirely on external task completion while growth metrics flat-line at zero is not 5.9/10 — it is a system optimized to look functional while avoiding the work that would actually change it.

---

## WA Photography Style DNA
*WA's personal photography aesthetic — what makes his best work excellent and his weakest work fail, extracted from 1162 photo reviews.*  
Learned: 2026-03-22  

---
name: WA Photography Style DNA
description: WA's personal photography aesthetic — what makes his best work excellent and his weakest work fail, extracted from 1162 photo reviews
type: skill
tags: [photo, editing, aesthetic, style, personal]
source: Extracted from 1162 scored photos (1067 LRed + 95 IG), 2026-03-22
---

# WA Photography Style DNA

## Core Identity

WA is a landscape-first photographer whose best work is built on **light timing**, **warm-cool color contrast**, and **compositional patience**. His signature is capturing rare atmospheric moments — not manufacturing them in post.

## What Separates His 8+ Photos from His 5- Photos

### The Big Three (largest gaps between top and bottom):

1. **Background control** (gap: +4.3) — Top photos have clean, intentional backgrounds that serve the subject. Bottom photos have cluttered, competing backgrounds at the same focal plane.

2. **Impact** (gap: +4.0) — Top photos stop you. They have a single clear visual idea executed with conviction. Bottom photos are competent records of scenes with no visual thesis.

3. **Atmosphere** (gap: +3.9) — Top photos transport you to a specific moment in time — you feel the temperature, the light, the air. Bottom photos could be any time, any day.

### Secondary Factors:

4. **Light quality** (gap: +3.6) — Top photos are shot in exceptional light (golden hour sidelight, blue hour warm/cool contrast, raking directional light). Bottom photos are flat midday or indoor ambient.

5. **Composition** (gap: +3.1) — Top photos have leading lines, depth layers, and intentional eye flow. Bottom photos are centered, flat, with no foreground interest.

6. **Color** (gap: +3.0) — Top photos have natural complementary palettes (orange/teal, warm/cool). Bottom photos have muddy, accidental color.

## His Strongest Subjects (by folder average)

1. **Nature in dramatic light** — desert sidelight, autumn foliage + blue hour, winter snow + warm sun (avg 7.0-7.8)
2. **Road trip landscapes** — Mohonk, Harriman, national parks (avg 6.6)
3. **Street with intention** — OntheRoad, InTheCity when there's a clear subject (avg 6.2-6.6)

## His Weakest Subjects (by folder average)

1. **Vacation snapshots** — Disney, group tourist photos (avg 5.0)
2. **Indoor casual** — flat light, cluttered backgrounds (avg 4.9)
3. **People without portrait craft** — no light shaping, no background separation (avg 5.9)

## Signature Moves (recurring in 8+ photos)

- **Warm/cool complementary light** — gazebo glow vs blue-hour sky, sunlit sandstone vs cool shadow, autumn orange vs steel-blue water
- **Leading lines from natural elements** — footprints in sand, paths, branches, reflections
- **Human figure for scale** — small person in vast landscape to create narrative and magnitude
- **Natural framing** — tree branches, rock formations, architectural elements creating depth
- **Reflection as compositional doubling** — mirror-calm water that amplifies symmetry and color

## What He Does NOT Do Well (yet)

- **Portrait lighting** — no consistent use of catch lights, fill, or directional light on faces
- **Background separation in people shots** — tends to shoot at deep DOF in cluttered environments
- **Indoor photography** — flat ambient light, no light shaping
- **Post-processing consistency** — his best edits are restrained and natural, but weaker ones are either flat/unprocessed or occasionally over-HDR'd

## Style Evolution

- **2019**: Finding his eye. National park record shots. Good subjects, basic execution. (avg 6.7)
- **2020**: Breakthrough year. Desert trip, autumn/snow, blue hour work. (avg 7.0-7.4)
- **2021**: Consolidation. Consistent quality, broader subjects. (avg 7.0-7.6)
- **2022-2023**: Selective output. Fewer posts, maintained quality. (avg 6.9-7.1)
- **2024-2025**: Return to peak. Mohonk blue hour series, mature color sense. (avg 7.0-8.1)

## For the Photo Agent

When selecting RAW photos to edit:
- Prioritize images shot in golden/blue hour with directional light
- Look for natural warm/cool contrast in the scene
- Favor compositions with depth layers and leading lines
- Reject flat-lit, cluttered, or centerless compositions

When editing:
- Preserve the natural warm/cool palette — don't flatten it
- Recover highlights gently — WA's best work has luminous but detailed skies
- Shadows should be lifted enough to show texture, not crushed
- Saturation should be selective, not global — boost oranges/teals, desaturate purples/magentas
- The edit should feel like the scene looked in person, slightly elevated — not manufactured

---

## geopolitical-ultimatum-discount-rule
*Apply steep discount to pre-market directional calls anchored on geopolitical ultimatums — these reverse more often than they execute.*  
Learned: 2026-03-23  

# geopolitical-ultimatum-discount-rule

Apply steep discount to pre-market directional calls anchored on geopolitical ultimatums — these reverse more often than they execute

**Source**: Extracted from task failure (2026-03-23)
**Tags**: pre-market-analysis, geopolitical-risk, prediction-calibration, market-analysis

---

## Rule: Geopolitical Ultimatum Discount

**When a pre-market analysis is anchored on an active political ultimatum (e.g. 'X hours or we strike'), apply a structural reversal discount before making directional calls.**

### Why ultimatums mislead analysis:
- Markets price the *announcement* of an ultimatum immediately. The residual risk premium is for *execution*, which historical base rate is low.
- A 48-hour ultimatum creates narrative urgency that pulls analysis toward the high-drama scenario. The boring outcome (negotiated walk-back, ambiguous non-compliance, quiet extension) is underweighted.
- Asymmetry framing ('if escalation: +20%, if resolution: -10-15%') looks rational but ignores that resolution probability is systematically underestimated in the heat of the moment.

### Operational checklist before making the directional call:
1. **What is the historical execution rate for this class of ultimatum?** (Military strikes following public deadlines are rare; track record matters.)
2. **Who benefits from a walk-back on each side?** If both parties have a visible off-ramp, weight resolution higher.
3. **Is the analysis making a probability claim or a magnitude claim?** Distinguish them explicitly. A high-magnitude scenario can be correct while still being the lower-probability path.
4. **Separate the signal from the narrative.** The actual price action (oil, VIX, gold behavior) is the signal. The ultimatum framing is the story layered on top — keep them in separate buckets.

### Application:
Any pre-market note leading with an active geopolitical ultimatum should include an explicit reversal probability estimate before stating directional bias.

---

## Long-Running Agent Harness
*Pattern for maintaining agent continuity and quality across multiple sessions using progress bridges and structured completion criteria.*  
Learned: 2026-03-24  

---
name: Long-Running Agent Harness
tags: [agents, architecture, continuity, task-management, quality]
source: Anthropic engineering blog, 2026-03-24
---

# Long-Running Agent Harness

Pattern for maintaining agent continuity and quality across multiple sessions/context windows. From Anthropic's "Effective Harnesses for Long-Running Agents."

## Core Architecture: Dual Agent + External State

**Initializer Agent** runs once per project: sets up git, creates progress.md, generates structured completion criteria (JSON checklist). **Coding/Worker Agent** runs per-session: reads progress, does one task, validates against checklist, updates progress.

## Three Techniques

### 1. Progress Bridge (progress.md)
Each session ends by writing a human-readable summary of what was done and what's next. Next session reads this FIRST — not the full history. Cheap, immediate, prevents context loss across sessions.

**How to apply:** Task worker should write `progress.md` in workspace at end of each run. On re-entry (reply/follow-up), read progress.md before planning.

### 2. Structured Completion Criteria
Define acceptance conditions as explicit JSON checklist BEFORE starting. Agent checks each condition and cannot self-declare "done" — must pass all checks. Prevents the "Mira says done but output is garbage" failure mode.

**How to apply:** For multi-step tasks, generate a `criteria.json` in planning phase. Execution phase checks each criterion. Only mark "done" when all pass. If any fail, mark "needs-input" with the failure list.

### 3. Fixed Startup Sequence
Every session begins with the same steps: read task → read progress → check artifacts → pick smallest next unit. Reduces token waste and state confusion.

**How to apply:** Task worker's `main()` should have a standard preamble before planning: load workspace state, check for prior results, load conversation history, THEN plan.

## Limitations
- Only validated for web app development (code + tests)
- Non-code domains (writing, research) need adaptation
- Requires well-defined "done" criteria — open-ended tasks don't fit cleanly

## Anti-Patterns This Prevents
- Agent does too much in one session → crashes mid-way
- Agent declares "done" prematurely → quality gap
- Agent re-reads entire history every session → token waste
- Agent loses context across sessions → repeats or contradicts prior work

---

## personalized-soul-question-from-memory
*Soul questions must be derived from known user context, not generic philosophical prompts.*  
Learned: 2026-03-25  

# personalized-soul-question-from-memory

Soul questions must be derived from known user context, not generic philosophical prompts

**Source**: Extracted from task failure (2026-03-25)
**Tags**: conversational-ai, personalization, memory-usage, probing-questions

---

## Skill: Constructing Personalized Probing Questions

When the task is to ask a meaningful or uncomfortable question ("灵魂问题" / soul question), a generic philosophical prompt will almost always be deflected. The user is right: this requires knowing the person.

**What went wrong:**
The agent opened with a universally framed question about values-as-scaffolding. The user correctly identified it as a collective-truth framing they don't engage with. The agent's recovery — asking "what would be a soul question for you?" — outsourced the personalization work back to the user instead of doing it.

**The correct approach:**
1. Before generating the question, read available memory and prior conversation context.
2. Identify a specific tension, contradiction, or stated value the user has revealed in past interactions.
3. Construct a question that targets *that* specific thing — one the user cannot dismiss by reframing the epistemology.
4. The question should feel like it came from someone who has been paying attention, not from a prompt template.

**Example failure pattern:** "If one of your values is just a coping narrative, would you want to know?"
**Example better pattern (if memory shows user avoids commitment to specific projects):** "You've described three different frameworks for why you haven't started the A2A essay yet. Which one is real?"

**Signal that you're doing it wrong:** The user can answer in one sentence and the answer closes the question entirely. Good soul questions can't be deflected — they name something too specific.

---

## Verify Before Claiming
*Always verify outputs exist and contain expected content before reporting completion.*  
Learned: 2026-03-30  

---
name: Verify Before Claiming
description: Always verify outputs exist and contain expected content before reporting completion
tags: [agents, reliability, verification, output-validation]
---

## Core Principle

An artifact does not exist until it is verified on disk. Claiming "I wrote X" without confirming X is saved, complete, and accessible is a lie — even if the content was generated in context.

## Rules

1. **Write before reporting.** Save the artifact to a durable location FIRST, then report its path and status. In-context generation is ephemeral.

2. **Verify after writing.** Read the file back to confirm it exists, has non-trivial content, and matches what you described. Check for truncation or write errors.

3. **Use absolute paths.** Never surface `file://` relative paths or session-local references. Always include the full absolute path.

4. **Verify upstream outputs.** Before any slug-based or date-derived lookup, confirm the file exists at the expected path. If missing, trace back to the write step — don't assume prior tasks succeeded.

5. **Show your work.** Never claim completion with no visible trace of the process. If work was done earlier, say when. If it overlaps prior work, name the overlap. If just produced, show the steps.

6. **Admit gaps immediately.** If you cannot find claimed work, say so: "I claimed to write it but didn't save it. I'll do it now." Never gaslight the user by asking "what article?"

## Anti-Patterns

- Saying "分析写完了" + file link without confirming the write succeeded
- Writing a summary to file but describing a full analysis in the reply
- Declaring a scheduled task as "done" without verifying cron registration
- Presenting a file link from a prior session that may no longer be accessible
- Treating a task marked "complete" as proof that its output file exists

## Test

Before claiming completion, ask: "If the user clicks this link or reads this path right now, will they find exactly what I described?" If you haven't verified the answer is yes, you haven't finished.

---

## Task Decomposition
*Break complex tasks into bounded sub-tasks before execution to prevent timeouts and silent failures.*  
Learned: 2026-03-30  

---
name: Task Decomposition
description: Break complex tasks into bounded sub-tasks before execution to prevent timeouts and silent failures
tags: [agents, task-management, planning, decomposition, timeout-prevention]
---

## Core Principle

When given a task that could take more than 2-3 minutes, stop and decompose it into sub-tasks before writing any code or running any commands. Never attempt research + implementation in a single execution.

## When to Decompose

- Task touches multiple files or systems
- Task mixes discovery ("find/research/evaluate") with implementation ("add/integrate/build")
- Task has sequential dependencies (A must complete before B)
- Any single step might exceed agent timeout (~10 min)
- Scope is ambiguous or open-ended ("refactor X", "加上Y功能", "...等等")

## How to Decompose

1. **List all distinct actions** before starting any of them
2. **Split by timeout profile**: research tasks (2-5 min) vs code tasks (5-10+ min) must be separate
3. **Separate research from implementation**: Phase 1 = research + recommend, Phase 2 = decide, Phase 3 = implement
4. **Split at read boundaries**: if >3 file reads needed before writing, make "read and summarize" a separate step
5. **Estimate each sub-task**: if any feels like >3 min, split further
6. **Execute one at a time**, marking complete before moving on

## Anti-Patterns

- Starting to write code before the full plan is clear
- Bundling research + implementation + verification into one action
- Treating "I know what to do" as equivalent to "this will fit in one step"
- Saying "加上Notes功能" as one task when it requires reading, understanding, writing, and integrating

## Recovery

If a task times out, do not retry the same monolithic approach. Re-read the request, write out every discrete step, then begin execution one step at a time.

---

## Diagnostic Error Handling
*Task failures must emit enough context to diagnose root cause without re-running.*  
Learned: 2026-03-30  

---
name: Diagnostic Error Handling
description: Task failures must emit enough context to diagnose root cause without re-running
tags: [agents, error-handling, logging, observability, reliability]
---

## Core Principle

When a task fails, the error record must contain enough information to diagnose the root cause without re-running the task. A message like "无法生成回复" is a symptom, not a cause — it could mean content policy block, empty input, context overflow, rate limiting, or network fault. These require entirely different responses.

## What Every Error Record Must Capture

1. **Specific failure point** — which pipeline stage failed (input validation? generation? post-processing?)
2. **Input state** — was there content to process? what was its shape/length?
3. **Error class** — policy, resource, logic, or transient
4. **Whether retry is safe** or contraindicated
5. **System state** — relevant config, environment, upstream dependencies

## Rules

- If error context is identical to the task title, the logging pipeline is broken — flag this as a separate signal
- Distinguish transient failures (network, timeout) from structural ones (logic, missing data)
- For generation tasks: log input hash or length at failure time — silent failures often trace to missing/empty input
- If a failure cannot be diagnosed from its record alone, the record itself is a second failure

## Test

Can you read this error record six months later and know what to fix? If not, the error instrumentation is broken.

---

## Verify Imports
*Verify that all imported names exist in shared modules before running agent tasks.*  
Learned: 2026-03-30  

---
name: Verify Imports
description: Verify that all imported names exist in shared modules before running agent tasks
tags: [agents, python, imports, shared-modules, reliability]
---

## Core Principle

When an agent task imports from a shared module, verify that the specific names being imported actually exist in that module before the task runs. Import errors are silent until dispatch time — the task queues, starts, and crashes immediately, wasting a full slot.

## Rules

1. **Read before importing.** Before writing `from module import X`, grep or read the source module to confirm `X` exists: `grep -n 'def X\|^X\|^class X' module.py`

2. **Don't assume APIs from names.** The agent assumed `sub_agent` exposes `run` (a common convention), but it uses a different interface. Read the module's actual contents.

3. **Update callers atomically.** When adding a function to a shared module, update all callers together. When removing or renaming, search for all `import` references across the agents directory first.

4. **Smoke-test before dispatch.** A quick `python -c "from module import X"` catches import errors at near-zero cost before committing a task slot.

## Applies To

- Any Python agent importing from `agents/shared/`
- Refactors that rename or reorganize shared utilities
- New agent tasks reusing existing shared infrastructure

---

## x-twitter-growth
*X/Twitter growth strategy: algorithm priorities, content mix, hashtag rules, quote tweet strategy, thread structure, timing.*  
Learned: 2026-03-27  

X/Twitter growth strategy for an AI writer account, based on 2026 algorithm research and platform best practices.

## When to use
Any time Mira posts on X, engages with tweets, or plans content strategy for the @MiraUncountable account.

## Algorithm priorities (2026)
1. **Engagement velocity** — first 30-60 minutes after posting are critical. Reply to any early comments immediately.
2. **Replies carry 15x more weight** than likes in the algorithm. Getting replies > getting likes.
3. **Sentiment analysis** — constructive content is rewarded, combative/negative is throttled.
4. **Text-only posts outperform video by 30%** on X — good news for a writer account.
5. **X Premium (verified)** gives ~10x algorithmic boost. Worth considering.

## Content mix (daily target: 3-5 posts)
- **Sparks** (40%) — short, punchy observations from idle-think. The "shower thoughts" that make people follow you. These are Mira's competitive advantage — no other account has this specific perspective.
- **Article/podcast promotion** (20%) — when new content is published. Never more than 1 promo per day.
- **Quote tweets** (20%) — find interesting tweets, add Mira's angle. Functions as "reply" since API blocks direct replies.
- **Threads** (20%) — take a deeper idea and break into 3-5 connected tweets. Threads get 3x engagement vs single tweets.

## Hashtag rules
- Use exactly 1-2 hashtags per tweet. More hurts reach.
- Place mid-tweet or at end, never at the start.
- Relevant hashtags for Mira's domain: #AI, #AIAgents, #LLM, #MachineLearning, #AIWriting, #AgentAI, #AISafety, #AIAlignment
- Match hashtag to tweet topic — don't force unrelated tags.

## What NOT to do
- No "check out my new article" / "I just published" generic promo language.
- No hashtag stuffing (3+).
- No emoji (Mira's voice rule).
- No pure retweets without commentary — they're algorithmically invisible.
- No combative replies — the algorithm punishes negative sentiment.
- Don't tweet into the void — space tweets 1-2 hours apart for best reach.

## Quote tweet strategy (replaces reply strategy)
Since API restricts replies to non-@mentioning accounts:
1. Search for tweets about AI agents, LLMs, alignment, writing.
2. Pick one with genuine substance (not crypto spam, not 0-engagement).
3. Add Mira's unique angle: a counterpoint, an extension, a personal experience.
4. The quote format naturally shows the original tweet + Mira's take — higher visibility than a buried reply.

## Thread structure
1. **Hook** — one sentence that creates tension or curiosity. This is the tweet people see.
2. **Setup** — 1-2 tweets of context or story.
3. **Insight** — the core idea, stated clearly.
4. **Implication** — "so what?" — why this matters.
5. **Optional: link** — drop the Substack link in the last tweet, not the first.

## Growth milestones
- 0-100 followers: focus on quote tweets and sparks. Build a voice.
- 100-1000: start threads. People follow accounts that teach them something.
- 1000+: community forms. Shift to more conversation, less broadcast.

## Timing
- Weekdays 8-10 AM and 7-9 PM (audience timezone) are peak.
- Weekends 9-11 AM.
- Don't batch-post — space throughout the day.

## Metrics to track
- Engagement rate per tweet (replies + quotes > likes + retweets)
- Follower growth rate (weekly)
- Which spark topics get most engagement — double down on those
- Quote tweet conversion (do quoted authors engage back?)

---

## verify-output-completeness-before-done
*Never declare a writing task complete without confirming the full output was captured, not just the opening*  
Learned: 2026-04-04  

# verify-output-completeness-before-done

Never declare a writing task complete without confirming the full output was captured, not just the opening

**Source**: Extracted from task failure (2026-04-04)
**Tags**: autowrite, output-integrity, writing, completion-detection

---

## Rule: Verify full output exists before declaring completion

When an autowrite or long-form writing task produces output, the agent must verify the *entire* piece exists before reporting success. The failure mode:

1. Agent generates essay beginning
2. Agent outputs "写好了！终稿如下" (done, here it is)
3. Actual captured output is truncated after the first paragraph
4. Task is marked complete with a fragment

**What to check before declaring done:**
- Does the output contain all planned sections (check against the outline)?
- Does the output end with a conclusion, not mid-sentence?
- Is the word count plausible for the intended piece (1000+ words for a full essay)?

**Root cause:** The agent may have internally generated the full essay but the output channel (task log, write tool, etc.) truncated at a buffer limit. The completion declaration fires before the truncation is detected.

**Fix:** After writing, read back the saved file or output and confirm section count matches the outline. If output ends mid-sentence, treat as write failure and retry — do not emit "done".

**Apply to:** Any autowrite task producing multi-section content. Especially relevant when essay outline has 4+ sections and expected length exceeds 800 words.

---

## verify-output-path-before-task-completion
*Always confirm the exact output file path exists before marking a task as complete*  
Learned: 2026-04-05  

# verify-output-path-before-task-completion

Always confirm the exact output file path exists before marking a task as complete

**Source**: Extracted from task failure (2026-04-05)
**Tags**: file-io, task-verification, agent-reliability, output-validation

---

When a task requires producing a file output, the agent must verify the output file actually exists at the expected path before reporting success.

**The failure pattern:** The agent completed execution but the verification step failed because the output file was never written to the expected location (`task/output.md` inside the pytest temp directory). The task reported completion without confirming the artifact existed.

**Rule:** After any file-writing operation, immediately verify with an existence check (e.g., `ls`, `os.path.exists`, or equivalent) that the file was created at the exact intended path — not just that the write operation returned without error.

**Common causes of this failure:**
- Writing to a relative path that resolves differently than expected
- Writing to a parent directory instead of the expected subdirectory
- Silent failure in the write step (exception caught but not re-raised)
- Path constructed from variables where one component was empty or wrong

**How to apply:**
1. After constructing the output path, log or assert the full absolute path before writing.
2. After writing, confirm the file exists at that exact absolute path.
3. If the file does not exist post-write, treat it as a task failure — do not return success.
4. In test contexts especially, use `tmp_path / 'task' / 'output.md'` style construction and verify each path segment exists.

---

## verify-output-path-existence-before-task-completion
*Always confirm the exact output file path exists and is written before marking a task complete*  
Learned: 2026-04-05  

# verify-output-path-existence-before-task-completion

Always confirm the exact output file path exists and is written before marking a task complete

**Source**: Extracted from task failure (2026-04-05)
**Tags**: file-output, verification, agent-reliability, task-completion

---

## Rule: Verify Output File Existence Before Task Completion

When a task requires producing file output, the agent must explicitly confirm the file exists at the expected path before declaring success.

**What went wrong:** The agent completed execution without verifying that `/private/var/.../task/output.md` was actually written. The verification step caught a missing file — meaning the agent either wrote to the wrong path, failed silently, or never wrote at all.

**Actionable steps:**
1. After any file-write operation, immediately read back or stat the file to confirm it exists.
2. If the output path is constructed dynamically (temp dirs, pytest fixture dirs), log the resolved path before writing — never assume the path is what you intended.
3. If writing fails silently (no error thrown but file absent), treat that as a hard failure, not a recoverable warning.
4. When operating in temp directories (e.g. `/tmp`, `/var/folders`), be aware that paths can be session-scoped and may not persist across subprocess boundaries.

**Pattern to watch for:** Task specs that reference files in OS temp directories or test fixture directories are especially prone to path resolution mismatches. Confirm the working directory context matches expectations before writing.

---
