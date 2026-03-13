# Skills (17 learned)

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

## quote-verification
*Verify any attributed quote via external search before including it in output.*  
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

## citation-verification
*Never output unverified citations — use search tools first or mark [未验证]*  
Learned: 2026-03-10  

When producing any factual claim that includes a specific source (author, paper title, book, year, quote), apply this rule:

1. **Default stance: assume you're wrong.** Your training data contains plausible-sounding but incorrect citations. Treat every citation from memory as suspect.

2. **Before writing a citation, ask: can I verify this right now?** If you have WebSearch or WebFetch available, USE THEM. The cost of a 5-second search is near zero; the cost of a wrong citation is trust destruction.

3. **If you don't verify, you must label.** Any citation not confirmed via external tool gets tagged `[未验证]` or `[unverified]`. No exceptions. This applies to author names, publication years, page numbers, and direct quotes.

4. **Why this matters mechanistically:** The failure mode is "generation inertia" — the model retrieves a plausible-sounding completion and commits to it before checking. Having search tools doesn't help if the generation pipeline never pauses to invoke them. The skill is the pause itself: interrupt the generation flow at citation boundaries and route to verification.

5. **Partial knowledge is the most dangerous case.** When you "kind of know" a reference, you're most likely to confuse details (wrong year, wrong co-author, wrong journal). These are harder for users to catch than fully fabricated citations. Extra vigilance on partial-match memories.

Rule of thumb: If you wouldn't bet $100 on the citation being exactly right, verify or label.

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

## Verify Target Before Editing
*When a UI change doesn't take effect, the first hypothesis must be "wrong file", not "broken build system".*
Learned: 2026-03-11

### The failure pattern
User reports a UI issue using a label (e.g., "threads don't collapse"). You find a file with a matching name (e.g., `ThreadListView.swift`) and start editing it. Multiple rounds of changes have no effect. You blame the build system, caching, iCloud sync, SwiftUI bugs — everything except the simplest explanation: you're editing the wrong file.

### The root cause
Mapping user-facing labels to source files requires verification, not assumption. A tab labeled "Threads" in the app might be implemented by `TasksView.swift`, not `ThreadListView.swift`. A section the user calls "comments" might live in `ReportDetailView`, not `CommentsView`.

### The rule
1. **Before editing**: Confirm which source file renders the screen the user is looking at. Trace the navigation path: tab bar → which View struct → which sub-views. When in doubt, ask for a screenshot.
2. **After first failed edit**: If one round of changes has zero visible effect, immediately ask: "Am I editing the right file?" Do NOT proceed to hypothesize about build caching, framework bugs, or sync issues until you've ruled out wrong-file.
3. **Screenshot early**: When the user describes a UI problem, request or look at a screenshot before writing any code. 5 seconds of visual confirmation prevents 30 minutes of editing the wrong file.

### Why this keeps happening
- File naming conventions create false confidence (`ThreadListView` sounds like it must be the thread list)
- Sunk cost: after 2 rounds of edits, it feels wasteful to question the target
- Build-system blame is a comfortable explanation that doesn't require admitting the error

### Generalized principle
When repeated actions produce zero effect, question the target before questioning the mechanism. This applies to: editing code, debugging, sending messages to wrong endpoints, writing to wrong config files.

---
