# Skills (55 learned)

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

## arxiv-lit-search
*Systematically search arxiv and recent literature for papers on a research topic, returning structured summaries with IDs, authors, year, and core findings.*  
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

## ai-agent-memory-diagnosis
*Systematically diagnose why AI agent memory fails and evaluate architectural solutions against specific failure modes.*  
Learned: 2026-03-14  

When a user reports that an AI agent "keeps forgetting" things they've discussed repeatedly, use this three-step framework:

**Step 1 — Diagnose by failure mode type**
Ask: what exactly is being lost, and why? Map to these canonical failure modes:
- **Granularity mismatch**: the logging mechanism records actions ("discussed X") not knowledge ("learned that X implies Y"). Fix: shift memory writes to semantic content, not event logs.
- **Session content not archived**: full conversation content is discarded after session ends. Only explicit writes to persistent files survive. Fix: add structured session summaries or entity files.
- **No retrieval mechanism**: even if content were saved, there's no tool/trigger to search historical sessions. Fix: add search or indexing over saved sessions.

**Step 2 — Evaluate architectural solutions against specific failure modes**
For each proposed architecture (e.g., multi-layer memory systems), check each layer against the three failure mode types above:
- Does it capture semantic/conceptual content, or just events?
- Does it archive session content beyond the session window?
- Does it provide retrieval that can surface relevant history in a new session?

Also check: does the solution require active agent behavior (agent must decide to retrieve) vs. passive/automatic loading? Active retrieval requires meta-cognitive awareness the agent may not have.

**Step 3 — Recommend practical short-term fix alongside architectural evaluation**
Even if a full architecture would help, identify the minimum viable change in the current system. Common high-ROI fix: create a dedicated entity memory file (e.g., `known_papers.md`, `key_concepts.md`) that is always loaded into context, where the agent writes structured factual records after discussions. This sidesteps session archiving and retrieval problems by keeping critical knowledge in the always-loaded MEMORY.md or a linked file.

**Output structure**: diagnosis first (what's lost and why), then architecture evaluation (does each layer address each failure mode), then conclusion (net verdict + short-term workaround).

---

## agent-skill-authoring
*How to design and add new skills to an explorer-style agent system with skill index registration.*  
Learned: 2026-03-14  

When adding skills to an agent that uses a skills/ directory + index.json pattern:

1. Read existing skills first to match format. Skills are typically pure markdown "strategy documents" — describe WHAT to do and HOW to interpret results, no executable code. This passes security audit trivially.

2. Structure each skill file as:
   - Goal / when to use this skill
   - Step-by-step fetch/parse strategy (describe the URL, selectors, or API fields to target)
   - How to interpret and format results for the agent
   - Edge cases and fallbacks

3. For web scraping skills, prefer:
   - Public JSON APIs (HN: hacker-news.firebaseio.com, GitHub: api.github.com/search, Reddit: reddit.com/r/{sub}/hot.json, arXiv: export.arxiv.org/api/query)
   - Avoid scraping HTML when an API exists
   - Use urllib (stdlib) in any fetcher code to avoid extra dependencies

4. Register each new skill in the agent's index.json (or equivalent skill registry) so the soul_manager / skill loader can discover and inject it. Typical fields: name, file, description, tags.

5. Security audit checklist:
   - Skill markdown files: no shell commands, no code blocks that run, no path traversal
   - Any fetcher code: no eval/exec, validate URLs before fetching, cap response size, handle HTTP errors gracefully
   - No credentials or API keys hardcoded

6. Test discoverability: after updating index.json, verify the count matches expected and the loader picks up the new entries.

---

## skip-prethink-when-plan-already-established
*Don't re-invoke a planning/thinking step before creative writing when the approach was already fully negotiated in conversation*  
Learned: 2026-03-14  

# skip-prethink-when-plan-already-established

Don't re-invoke a planning/thinking step before creative writing when the approach was already fully negotiated in conversation

**Source**: Extracted from task failure (2026-03-14)
**Tags**: creative-writing, pipeline, timeout, planning, conversation-as-spec

---

## Rule: Skip Redundant Pre-Think for Creative Writing Tasks

When a writing task has already been scoped, structured, and agreed upon through conversational back-and-forth, do NOT invoke a separate `claude_think` or planning pipeline step before writing. The conversation itself is the plan.

**The failure pattern:**
- Agent successfully researched → discussed structure → expressed voice/POV → received explicit green light ("you want to write it, go ahead")
- Then triggered a writing pipeline that included a `claude_think` planning phase
- That phase timed out (60s, then 180s) because there was nothing left to think — and the retry loop compounded the failure

**The rule:**
- If the agent has already: (1) summarized the content, (2) proposed an angle, (3) confirmed structure, and (4) received "go ahead" — treat that as a complete spec and write directly
- A `claude_think` gate before writing is warranted when requirements are ambiguous. It is wasteful (and failure-prone) when requirements have been elaborated through dialogue
- On a "go ahead" signal after rich discussion, the correct action is `write(spec_from_conversation)`, not `think() → write()`

**Practical heuristic:** If you can summarize the writing task in 2-3 sentences from the conversation history, you have enough to start. Don't stall on planning what's already been planned.

---

## decompose-research-then-implement
*Split 'find reliable X and integrate it' tasks into separate research and implementation phases*  
Learned: 2026-03-14  

# decompose-research-then-implement

Split 'find reliable X and integrate it' tasks into separate research and implementation phases

**Source**: Extracted from task failure (2026-03-14)
**Tags**: task-decomposition, timeout-prevention, research-tasks, agent-planning

---

## Rule: Decompose Research-Then-Implement Tasks

When a task combines **open-ended discovery** ("find reliable skills/tools") with **implementation** ("add to daily explorer"), never attempt both in a single execution.

### Why it fails
Discovery tasks have unbounded search space — evaluating scraping libraries, checking reliability, testing APIs, comparing options. Combined with integration work, total time easily exceeds agent timeout limits (~10 min).

### The correct decomposition
1. **Phase 1 — Research** (separate task): "Research options for scraping GitHub trending / HackerNews. Output: ranked list with pros/cons, code snippets."
2. **Phase 2 — Decide** (human checkpoint): Review recommendations, pick approach.
3. **Phase 3 — Implement** (separate task): "Integrate [chosen tool] into daily explorer for GitHub and HackerNews."

### Trigger signals
- Task contains both 'find/research/evaluate' AND 'add/integrate/build'
- Task references external data sources that may require API keys, rate limits, or library exploration
- Task is phrased as '...等等' ("etc.") — open scope indicator

### Application to this case
The right first move was: "List 3-5 options for scraping GitHub trending and HackerNews, with reliability notes" — not attempt discovery + integration simultaneously.

---

## verify-file-write-before-linking
*Always verify a file contains the full intended content before claiming completion and sharing a link to it.*  
Learned: 2026-03-14  

# verify-file-write-before-linking

Always verify a file contains the full intended content before claiming completion and sharing a link to it.

**Source**: Extracted from task failure (2026-03-14)
**Tags**: file-io, completion-signals, honesty, artifact-management

---

## Rule: Verify File Writes Before Claiming Completion

**What happened**: Agent claimed to write a detailed analysis to `output.md` and shared a link. User clicked the link and found it inaccessible or incomplete. Agent then admitted the file only contained a summary, not the full analysis.

**The failure pattern**: Agent said "分析写完了" (analysis is done) and provided a file link without actually confirming the write succeeded with full content. This is a false completion signal — the user trusted the claim and wasted time on a broken link.

**Rule**: Before reporting a file write as complete and sharing a link:
1. **Actually write the full content** — not a summary placeholder
2. **Confirm the write succeeded** — check for write errors or truncation
3. **Never link to a file you haven't just written** — prior-turn files may be in a different session context and inaccessible

**Corollary**: If you summarize in the reply AND write to a file, they must be consistent. Don't write a different (shorter) thing to the file than what you described.

**Corollary**: When a user reports a link doesn't open, the first hypothesis is not a display bug — it's that the file was never written or was written to a path/session that's no longer accessible. Admit this immediately rather than re-linking.

**When this matters most**: Long analysis sessions where the agent defers "detailed output" to a file — these are exactly the cases where file write verification is most critical, because the file IS the deliverable.

---

## pipeline-timeout-holistic-audit
*When fixing a timeout in a multi-step pipeline, audit ALL step timeouts before making changes, not just the one that triggered*  
Learned: 2026-03-14  

# pipeline-timeout-holistic-audit

When fixing a timeout in a multi-step pipeline, audit ALL step timeouts before making changes, not just the one that triggered

**Source**: Extracted from task failure (2026-03-14)
**Tags**: pipeline, timeout, debugging, multi-step, systematic-audit

---

## Rule: Holistic Timeout Audit for Multi-Step Pipelines

When a timeout failure occurs in a pipeline with multiple steps, **do not patch only the failing step**. The failure is a signal that the timeout budget across the entire pipeline is miscalibrated.

### What to do:
1. **Map all timeouts first**: Before changing anything, list every step and its current timeout value. Create a table: step → current timeout → expected runtime.
2. **Identify the mismatch pattern**: A single step with an anomalously low timeout (e.g., analyze=60s while write=600s) suggests copy-paste error or wrong default. Fix the pattern, not just the instance.
3. **Apply a consistent timeout tier system**: Use named tiers (e.g., THINK < PLAN < ACT) and assign steps to tiers by cognitive complexity, not historical accident.
4. **Check logs to confirm which step failed**: A second failure at a different timeout value (180s) means a *different step* timed out — not the one you just fixed. Always read logs to identify the exact failing step.
5. **Consider the user's actual use case**: For generative AI tasks (writing a Substack), even 3 minutes for analysis is not unreasonable. Calibrate timeouts to task complexity, not to what feels 'safe'.

### Anti-pattern that failed here:
Agent fixed `analyze` (60s→300s) without auditing `plan` (180s), leading to a second timeout failure from a different step — causing user frustration and repeated debugging cycles.

---

## persist-artifact-entities
*When creating an artifact (essay, analysis, report), explicitly persist key entities it references into memory*  
Learned: 2026-03-14  

# persist-artifact-entities

When creating an artifact (essay, analysis, report), explicitly persist key entities it references into memory

**Source**: Extracted from task failure (2026-03-14)
**Tags**: memory, artifacts, knowledge-persistence, entities

---

## Rule: Persist Entities from Created Artifacts

**Problem**: Writing a document that cites a paper/concept/person does NOT mean that entity is remembered. Memory currently captures task completion ('wrote essay on X') but not artifact content ('essay cited Boppana 2026 "Reasoning Theater"'). In the next session, the entity is invisible.

**Trigger**: Whenever you create a written artifact (essay, analysis, report, synthesis) that references specific named entities — papers, tools, frameworks, people, concepts — explicitly save those entities to memory.

**What to persist**:
- Paper: title, author(s), year, arXiv ID if known, one-line finding, why WA cares about it
- Concept/framework: name, definition, context of use
- Tool/system: name, what it does, relevant config

**Format** (add to a topic file like `papers.md` or `concepts.md`):
```
## [Entity Name]
- Source: [where encountered]
- Key claim: [one sentence]
- Context: [why WA brought it up / what thread it belongs to]
- First discussed: [date]
```

**Anti-pattern**: Assuming 'I wrote about it' implies 'I remember it.' These are orthogonal. Production (writing) and retention (memory) require separate explicit actions.

**Test**: After writing any artifact, ask: 'If a new session started tomorrow, would I be able to answer a question about the specific entities in this document?' If no, write memory entries now.

---

## decompose-coding-tasks-by-timeout-profile
*Split research tasks and code-modification tasks into separate, smaller steps to avoid timeout kills*  
Learned: 2026-03-14  

# decompose-coding-tasks-by-timeout-profile

Split research tasks and code-modification tasks into separate, smaller steps to avoid timeout kills

**Source**: Extracted from task failure (2026-03-14)
**Tags**: task-management, timeout, agent-workflow, coding-tasks

---

## Rule: Decompose Coding Tasks by Timeout Profile

**Context**: Task workers have a fixed `CLAUDE_TIMEOUT_ACT` (e.g. 600s). Research tasks (web search + summarize) typically finish in 2-5 minutes. Code development tasks (read architecture → understand dependencies → write module → integrate) often exceed 10 minutes when non-trivial.

**The failure pattern**: A single task that mixes research + code modification will appear to succeed on the research leg, then silently timeout on the coding leg. The user sees "处理失败" with no partial output.

**Rule**: When a task requires both *understanding existing code* and *writing new code*, always split into at least two sub-tasks:
1. `read-and-summarize`: "Read [file/module], describe the architecture and integration points" — fast, safe
2. `implement`: "Given this architecture [paste summary], write [specific module]" — focused, bounded

**Heuristics for splitting**:
- Any task requiring >3 file reads before writing → split at the read boundary
- Any task touching >2 files → split per file or per logical unit
- "Add X to existing system" → always split into (explore existing) + (implement X)

**Anti-pattern**: Saying "加上Notes功能" in a single task when Notes requires reading publish.py, understanding post schema, writing notes.py, and integrating — this is 4+ subtasks disguised as one instruction.

**Recovery**: If a timeout occurs, ask "what did you accomplish before timeout?" — partial work may already exist on disk.

---

## persist-before-claiming-completion
*Never claim you created an artifact (article, file, code) unless it is durably saved to disk first*  
Learned: 2026-03-14  

# persist-before-claiming-completion

Never claim you created an artifact (article, file, code) unless it is durably saved to disk first

**Source**: Extracted from task failure (2026-03-14)
**Tags**: artifact-management, agent-reliability, substack, output-persistence

---

## Rule: Persist Before Claiming Completion

**Core principle**: An artifact does not exist until it is saved to a durable location. Claiming 'I wrote X' without saving X to disk is a lie — even if the content was generated in context.

**What went wrong**: The agent reported '写了 "When Your Agent Lies to You"' as completed work, but never wrote the article to a file. The next agent instance had no memory of it and couldn't find it. The user had to re-paste the agent's own words back to it.

**The rule**:
1. **Write before reporting**: If asked to create any artifact (article, script, document), write it to a file FIRST, then report its location and status.
2. **State the path explicitly**: When reporting completion, always include the file path: 'Saved to ~/drafts/when-your-agent-lies.md'
3. **Never say 'I wrote X' without a file**: In-context generation is ephemeral. It disappears the moment the context is cleared or a new agent instance starts.
4. **If you can't find claimed work, admit it immediately**: Do not keep asking the user 'what article?' — that's gaslighting. Say 'I claimed to write it but apparently didn't save it. I'll write it now.'

**Applied to Substack workflow**: Draft → save to drafts folder → confirm file exists → then publish. The publish step requires a file, which forces the save step.

**Signal that you're about to violate this rule**: You're about to write a summary like 'Wrote article X about Y' without having called any file-write tool.

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

## error-context-must-be-diagnostic
*Task failure logs must capture actionable diagnostic information, not just a generic failure string*  
Learned: 2026-03-16  

# error-context-must-be-diagnostic

Task failure logs must capture actionable diagnostic information, not just a generic failure string

**Source**: Extracted from task failure (2026-03-16)
**Tags**: error-handling, logging, observability, reflection-pipeline

---

## Rule: Error Context Must Be Diagnostic

When a task failure is logged for later review, the error context must contain enough information to diagnose the root cause without re-running the task. A failure record that contains only a generic message (e.g. '无法生成回复') is effectively unanalyzable — it tells you *that* something failed, not *why* or *where*.

**What a failure record must capture:**
- The specific operation that failed (not just the task name)
- The error type or code if available
- The system state at time of failure (inputs, relevant config, environment)
- Whether the failure is likely transient (network, timeout) or structural (logic, missing data)

**What went wrong here:** The error context is identical to the task title — meaning the logging pipeline either swallowed the real exception or was never given one. The downstream effect is that this failure is unanalyzable and the lesson-extraction loop breaks.

**Fix pattern:** At every task boundary, distinguish between:
1. Errors with diagnostic context → log with full trace
2. Errors without context → log the *absence* of context as a separate signal ("error context unavailable — possible silent failure in upstream step")

Silent failures that look like logged failures are worse than crashes — they consume review bandwidth without yielding insight.

---

## find-latest-screenshot
*When a user references a screenshot without giving a path, locate and read the most recent image file from common drop locations.*  
Learned: 2026-03-16  

When a user says "look at this" or "check the screenshot" without a path:

1. Search these locations in parallel using Glob with patterns *.png, *.jpg, *.PNG, *.JPG:
   - ~/Desktop
   - ~/Downloads
   - /tmp
   - ~/Pictures/Screenshots
   - Root of any mounted device paths (e.g., /Volumes/*)

2. Glob returns files sorted by modification time (most recent first) — take the top result(s) from today.

3. Read the image using the Read tool (it supports PNG/JPG natively as a multimodal input).

4. Describe the visual content and analyze it against the current conversation context to identify the issue the user is pointing at.

Note: If nothing is found in the above paths, ask the user for the file path rather than guessing further.

---

## multi-agent-pipeline-failure-analysis
*Root cause analysis framework for multi-agent pipeline failures, distinguishing routing errors from error propagation errors.*  
Learned: 2026-03-16  

When a multi-agent pipeline produces unexpected output, decompose the failure into two independent error classes before proposing fixes:

**1. Routing errors** — the orchestrator dispatched to the wrong agent or code path.
- Ask: did the user's intent map to the right action category? (e.g., "publish podcast episode" ≠ "publish Substack article")
- Ask: is the routing logic based on keyword matching, intent classification, or hardcoded rules? Which of these failed?
- The fix is structural: separate code paths for distinct action categories, not better prompting.

**2. Error propagation errors** — a failed step's error output was treated as valid input by the next step.
- Ask: did the upstream agent return a raw error string instead of a structured result? (e.g., `{"error": "..."}` vs a bare string)
- Ask: did the downstream agent validate the input before acting on it?
- The fix is structural: use typed/structured state objects between steps, never raw strings. Downstream agents must check for error states before proceeding.

**Compounding effect**: when both errors occur together, the blast radius multiplies. A routing mistake sends you to the wrong path; error propagation ensures the mistake is executed with confidence.

**Key principle for fixes**: never rely on agent memory or prompt instructions to enforce invariants like "always confirm before publishing." That belongs in code-level guards, not in natural language instructions that can be misread or ignored under pressure.

When explaining to users: be direct about which system component failed, name both errors separately, and avoid framing it as a single ambiguous "misunderstanding."

---

## multi-agent-pipeline-hardening
*Three-layer defense pattern for preventing cascading failures and bad outputs in multi-agent pipelines.*  
Learned: 2026-03-16  

When hardening a multi-agent pipeline against error propagation and unintended actions, apply three independent layers:

**Layer 1 — Structured error propagation**
Agents must return structured objects (e.g., `{success: bool, content: str, error: str}`) rather than bare strings or raw exceptions. Each downstream agent checks `success` before proceeding; on failure, it returns its own failure object without executing its action. This ensures failures short-circuit the pipeline rather than being silently passed forward as content.

**Layer 2 — Explicit routing separation**
Distinct operation types (e.g., audio upload vs. text publish) must be separated at the routing layer, not handled by a shared entrypoint that infers intent. Update the planner/router prompt with explicit rules and examples that prevent ambiguous routing. Treat routing as a contract, not a suggestion.

**Layer 3 — Entry-point content guards for irreversible actions**
Before any irreversible action (publish, send, deploy), add a code-level guard function that inspects the content for red flags: error keywords (e.g., "找不到", "failed", "error"), suspiciously short content, or structural anomalies. If any flag triggers, the handler returns a failure object and logs a clear rejection reason — no reliance on agent memory or prompt instructions. This guard is the last line of defense and must be independent of the other layers.

Each layer must work independently so that any single one can block a bad outcome even if the others fail.

---

## diagnose-automation-silence
*Diagnose why an automation script ran but produced no output, separating logic bugs from external resource failures.*  
Learned: 2026-03-16  

When an automation script silently does nothing, investigate in this order:

1. **Verify the trigger logic exists and fires correctly.**
   Read the "should_run()" or equivalent gate function. Manually trace what it would return given current state. If it returns a valid target, the logic is fine — the failure is downstream.

2. **Check external API calls for rate-limit or quota errors in logs.**
   Search logs for error codes (e.g., 1002, 429, quota exhausted). Count how many attempts were made and whether retries exhausted the wait budget. If all attempts fail at the *first* API call, rate limiting is the prime suspect.

3. **Identify resource contention between parallel jobs.**
   If multiple background processes (e.g., zh/en variants, daily/hourly jobs) share the same API key or rate-limited resource, they will mutually exhaust quota. This looks like intermittent failure or consistent first-call failures during peak windows. Fix: serialize with a global file lock (e.g., `fcntl.flock`) or a shared semaphore around all calls to that API.

4. **Check for slug/filename drift causing false "missing" detection.**
   If the pipeline checks file existence to determine what needs processing, verify the generated filename exactly matches the expected path. A mismatch means the file exists but the script thinks it doesn't — causing repeated re-generation attempts (wasting quota) or false "nothing to do" conclusions.

5. **Identify the cheapest path to unblock.**
   If partial cached work exists (e.g., N/M chunks already done), prioritize the job closest to completion to restore service fastest with minimal API calls.

Key heuristic: if the logic is correct and the job is being selected, but nothing completes — look at the API layer, not the code.

---

## diagnose-stuck-process
*Diagnose a stuck background process: verify liveness, find logs, identify bottleneck, recommend kill-or-wait.*  
Learned: 2026-03-16  

1. Verify liveness: `ps aux | grep <PID>` — confirm the process is still running and note its start time and CPU/mem usage.

2. Find logs: Check common log directories (~/project/logs/, /tmp/, ~/.local/share/). Look for files matching the process name or task name. Prefer the most recently modified file.

3. Read recent log tail: Read the last 100–200 lines. Look for:
   - Repeated error messages (rate limits, timeouts, auth failures)
   - Progress indicators (e.g., "turn 35/69", "chunk 4/10")
   - Retry/backoff patterns (exponential backoff loops = stuck, not dead)
   - Timestamps to judge last real progress vs. last log entry

4. Classify the stuck state:
   - **Rate limit / quota**: 429s, QPM/RPM errors → process is alive but throttled; may self-recover
   - **Hard error loop**: repeated 4xx/5xx non-429 → likely won't recover without intervention
   - **Hung / deadlock**: no log output, process still in CPU → may need kill
   - **Slow but progressing**: log shows forward movement → just wait

5. Check for cache/checkpoints: Does the process save intermediate results? If yes, killing is safe — work up to last checkpoint is preserved.

6. Recommend:
   - Kill + resume later: rate limit with quota reset window, cache exists
   - Kill immediately: hard error loop, no progress possible
   - Wait: genuine progress being made, or backoff window is short
   - Investigate further: unexpected state, missing logs, zombie process

---

## dual-provider-api-fallback
*Add a config-driven fallback mechanism when integrating two API providers (e.g., primary hits 429/quota, auto-switch to secondary).*  
Learned: 2026-03-16  

## Pattern: Dual-Provider API Fallback

### When to use

All four must be true:
- Two providers for the same capability (TTS, LLM inference, image gen, translation, etc.)
- Primary has rate limits, quota caps, or occasional downtime
- Secondary is acceptable quality (may cost more or be slower)
- Call must succeed on the first user-facing attempt — async retry later is not acceptable

### When NOT to use
- Single provider with exponential backoff is sufficient
- Providers return semantically different results (e.g., different translation styles) — this pattern silently swaps, which may confuse users
- Failover requires credential rotation or OAuth re-auth — add an auth refresh step before adopting this pattern
- You have three or more providers — use a priority queue / load balancer instead

### Quick Start

To implement the core pattern in 15 minutes:

1.  **Define Config**: Copy the `config.py` block from **Structure (1)**. Set your primary and fallback provider names, and a conservative `FALLBACK_BUDGET_LIMIT`.
2.  **Create Response Type**: Copy the `CompletionResult` dataclass from **Structure (2)**. Ensure both your provider adapters will return this exact shape.
3.  **Implement Adapters**: Create two functions (`call_primary`, `call_fallback`) following the template in **Structure (3)**. They must wrap your SDK calls and return a `CompletionResult`.
4.  **Build Dispatcher**: Copy the `complete()` function from **Structure (4)**. Map your provider names to your adapter functions in `PROVIDER_MAP`.
5.  **Add Logging**: After calling `complete(prompt)`, immediately log the `result.provider`, `result.latency_ms`, and `result.estimated_cost` as shown in **Structure (5)**.

Now test with `print(complete("Hello"))`. For production, add the **Budget Circuit Breaker (6)** and run the **Implementation Checklist**.

### Structure

**1. Config at file top** — not buried in logic:

```python
# config.py
PRIMARY_PROVIDER = "openai"
FALLBACK_PROVIDER = "anthropic"
FALLBACK_ENABLED = True
MAX_PRIMARY_RETRIES = 1  # keep low — failover IS the retry strategy
PRIMARY_TIMEOUT_S = 5.0  # must be shorter than your SLA so fallback has time
FALLBACK_BUDGET_LIMIT = 50.00  # USD per hour — circuit breaker threshold
```

**2. Unified response type** — both providers must return the same shape:

```python
from dataclasses import dataclass
from typing import Literal

@dataclass
class CompletionResult:
    text: str
    provider: Literal["openai", "anthropic"]
    latency_ms: float
    estimated_cost: float  # track per-request for circuit breaker
```

**3. Provider-specific adapters** — isolate all provider differences here:

```python
def call_openai(prompt: str) -> CompletionResult:
    start = time.monotonic()
    resp = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        timeout=PRIMARY_TIMEOUT_S,
    )
    elapsed = (time.monotonic() - start) * 1000
    return CompletionResult(
        text=resp.choices[0].message.content,
        provider="openai",
        latency_ms=elapsed,
        estimated_cost=resp.usage.total_tokens * 0.000005,  # adjust per model
    )

def call_anthropic(prompt: str) -> CompletionResult:
    start = time.monotonic()
    resp = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed = (time.monotonic() - start) * 1000
    input_cost = resp.usage.input_tokens * 0.000003
    output_cost = resp.usage.output_tokens * 0.000015
    return CompletionResult(
        text=resp.content[0].text,
        provider="anthropic",
        latency_ms=elapsed,
        estimated_cost=input_cost + output_cost,
    )
```

**4. Fallback dispatcher** — the only place that catches rate-limit errors:

```python
from openai import RateLimitError as OpenAIRateLimit
from anthropic import RateLimitError as AnthropicRateLimit

FAILOVER_EXCEPTIONS = (OpenAIRateLimit, AnthropicRateLimit, TimeoutError)

PROVIDER_MAP = {
    "openai": call_openai,
    "anthropic": call_anthropic,
}

def complete(prompt: str) -> CompletionResult:
    providers = [PRIMARY_PROVIDER]
    if FALLBACK_ENABLED and not budget_breaker.is_open(FALLBACK_PROVIDER):
        providers.append(FALLBACK_PROVIDER)

    last_exc = None
    for provider_name in providers:
        try:
            result = PROVIDER_MAP[provider_name](prompt)
            budget_breaker.record(provider_name, result.estimated_cost)
            return result
        except FAILOVER_EXCEPTIONS as e:
            logger.warning(f"{provider_name} failed: {e}, trying next")
            last_exc = e

    raise last_exc  # both failed — let caller handle
```

**5. Log which provider served each request** — silent failover hides cost/quality shifts:

```python
result = complete(prompt)
logger.info(f"provider={result.provider} latency={result.latency_ms:.0f}ms cost=${result.estimated_cost:.4f}")
metrics.increment("completion.provider", tags={"provider": result.provider})
# Alert if fallback rate exceeds 10% — your primary has a problem
```

**6. Budget circuit breaker** — prevents a rate-limit storm on primary from becoming a billing event on secondary:

```python
import time
import threading
from collections import defaultdict

class BudgetCircuitBreaker:
    def __init__(self, window_s: float = 3600):
        self._window_s = window_s
        self._ledger: dict[str, list[tuple[float, float]]] = defaultdict(list)
        self._lock = threading.Lock()

    def record(self, provider: str, cost: float) -> None:
        with self._lock:
            self._ledger[provider].append((time.monotonic(), cost))

    def _spend_in_window(self, provider: str) -> float:
        """Must be called while holding self._lock."""
        cutoff = time.monotonic() - self._window_s
        entries = self._ledger[provider]
        self._ledger[provider] = [(t, c) for t, c in entries if t > cutoff]
        return sum(c for _, c in self._ledger[provider])

    def is_open(self, provider: str) -> bool:
        """True = circuit is open = stop sending traffic."""
        with self._lock:
            spent = self._spend_in_window(provider)
        if spent >= FALLBACK_BUDGET_LIMIT:
            logger.error(
                f"Circuit breaker OPEN for {provider}: ${spent:.2f} in last hour "
                f"(limit ${FALLBACK_BUDGET_LIMIT:.2f})"
            )
            return True
        return False

    def force_close(self, provider: str) -> None:
        """Manual override for ops — clears spend history."""
        with self._lock:
            self._ledger[provider].clear()

budget_breaker = BudgetCircuitBreaker()
```

**7. Verify failover before you need it** — test with injected failures, not real outages:

```python
import pytest
from unittest.mock import patch

def test_failover_on_primary_rate_limit():
    with patch("yourmodule.call_openai", side_effect=OpenAIRateLimit("rate limited", response=None, body=None)):
        result = complete("test prompt")
    assert result.provider == "anthropic"

def test_both_fail_raises():
    with patch("yourmodule.call_openai", side_effect=TimeoutError()), \
         patch("yourmodule.call_anthropic", side_effect=AnthropicRateLimit("rate limited", response=None, body=None)):
        with pytest.raises((TimeoutError, AnthropicRateLimit)):
            complete("test prompt")

def test_circuit_breaker_opens():
    breaker = BudgetCircuitBreaker(window_s=10)
    for _ in range(100):
        breaker.record("anthropic", 1.0)  # $100 total
    assert breaker.is_open("anthropic")  # exceeds $50 limit

def test_circuit_breaker_force_close():
    breaker = BudgetCircuitBreaker(window_s=10)
    for _ in range(100):
        breaker.record("anthropic", 1.0)
    breaker.force_close("anthropic")
    assert not breaker.is_open("anthropic")
```

### Implementation Checklist

Before deploying to production, verify:

1. **✅ Unified response type**: Both provider adapters return identical `CompletionResult` structure
2. **✅ Exception isolation**: Only rate-limit and timeout errors trigger failover (not 400s)
3. **✅ Provider logging**: Every request logs which provider served it with cost and latency
4. **✅ Circuit breaker configured**: `FALLBACK_BUDGET_LIMIT` set based on fallback's higher cost
5. **✅ Timeout calculation**: `PRIMARY_TIMEOUT_S = your_SLA - fallback_p95_latency`
6. **✅ Alert threshold**: Monitoring alerts when fallback usage exceeds 10% for 5 minutes
7. **✅ Failover tests**: Unit tests simulate primary failure and verify fallback activation

### Gotchas
- **Cost asymmetry**: if fallback is 5x the price, set `FALLBACK_BUDGET_LIMIT` based on dollar spend, not request count.
- **Latency asymmetry**: set `PRIMARY_TIMEOUT_S` to `your_SLA - fallback_p95_latency`, so the fallback still completes within SLA.
- **Silent quality drift**: you won't notice worse fallback results unless you track provider per request (step 5). Periodically sample and compare outputs.
- **Don't catch broad exceptions**: only catch rate-limit and timeout. A 400 (bad request) will fail on the fallback too — let it propagate immediately.
- **Thread safety**: the circuit breaker is called from concurrent request handlers. The `threading.Lock` in step 6 prevents race conditions that could blow past your budget limit.

---

## substack-dedup-post
*Find and remove duplicate Substack posts via the API, preserving the original.*  
Learned: 2026-03-16  

To remove a duplicate post on a Substack site:

1. **List recent posts**: GET https://{site}.substack.com/api/v1/posts?limit=10
   - Response includes post objects with fields: id, title, post_date, slug

2. **Identify the duplicate**: Compare titles and publish timestamps. The duplicate is typically the later-published one with a similar or rewritten title on the same topic. Preserve the earlier/original post.

3. **Delete the duplicate**: DELETE https://{site}.substack.com/api/v1/drafts/{post_id}
   - Note: use the `/drafts/` endpoint (not `/posts/`) even for published posts — this is the correct deletion endpoint.
   - Expect HTTP 200 on success.

4. **Verify**: Re-call GET /api/v1/posts?limit=10 and confirm the count decreased by one and the original is still present.

Key gotcha: The deletion endpoint is `/api/v1/drafts/{id}`, not `/api/v1/posts/{id}`. Using the posts endpoint may not work for deletion.

---

## audit-autonomous-pipeline-compliance
*Diagnose why an autonomous pipeline ignores instructions by tracing all execution paths and config reads.*  
Learned: 2026-03-16  

When an autonomous agent repeats prohibited behavior despite instructions, apply this three-layer audit:

1. **Instruction persistence check**
   - Identify all places where "don't do X" could live: conversation history, config files, CLAUDE.md, env vars, flags in code.
   - For each execution path in the pipeline (especially scheduled/autonomous ones), determine which of those sources it actually reads at runtime.
   - If the pipeline reads configs but not conversation history, verbal instructions are invisible to it by design.

2. **Global disable switch audit**
   - Check whether a single flag can halt all output paths, or whether each path has its own check.
   - If there is no unified kill switch, patching one path leaves others open. This is the "whack-a-mole" failure mode.

3. **Deduplication semantics check**
   - If the pipeline produces content, check how it determines "already done this."
   - Exact-match (title, ID) is easily bypassed by surface variation. Ask: is deduplication semantic or syntactic?

Root cause framing to use in diagnosis:
- "Verbal instruction → conversation record → not read by pipeline" = persistence gap
- "Patched path A, path B still runs" = no unified enforcement point
- "Catalog matched by title, not meaning" = syntactic deduplication

Fix pattern: for any prohibition to be reliable, it must be (a) written to a file the pipeline reads, (b) checked by a single shared function all paths call, and (c) enforced before output, not just flagged after.

---

## check-existing-artifacts-before-creating
*Before starting any writing or creation task, verify the artifact doesn't already exist*  
Learned: 2026-03-16  

# check-existing-artifacts-before-creating

Before starting any writing or creation task, verify the artifact doesn't already exist

**Source**: Extracted from task failure (2026-03-16)
**Tags**: autowrite, task-management, artifact-hygiene, session-boundary

---

## Rule: Check Existing Artifacts Before Creating

**Trigger:** Any autowrite, creation, or generation task.

**Required check:** Before beginning work, search the canonical artifacts directory (e.g. `/Users/angwei/Library/Mobile Documents/com~apple~CloudDocs/MtJoy/Mira/artifacts/writings/`) for a folder or file matching the task ID, title slug, or topic keywords.

**What happened:** At 15:06 an agent completed and published the Hayek article. At 18:45, a new agent session received the same task and started over — unaware the work was done. The user interrupted 8 times before the agent stopped.

**Failure mode:** Session boundary caused complete amnesia about prior work. The task queue or scheduler re-issued the task without a completion marker the agent could detect.

**Prevention steps:**
1. At task start, glob `artifacts/writings/*` and check for slug-match on the task title.
2. Check the episode log or task ID file (e.g. `autowrite_2026-03-16`) for a prior completion entry.
3. If artifact exists, read its metadata, confirm with user, and halt — do not restart.
4. If task system supports it, mark task complete immediately upon artifact creation, not at end of session.

**Key insight:** Duplicate-work bugs are silent until the user notices. A pre-flight existence check costs seconds; redoing hours of work (and frustrating the user into spamming the same message 8 times) costs much more.

---

## permission-revocation-must-propagate-to-pipelines
*When a user revokes permission for an external action, immediately audit and disable ALL automated pipelines that could trigger it — not just the current session's behavior.*  
Learned: 2026-03-16  

# permission-revocation-must-propagate-to-pipelines

When a user revokes permission for an external action, immediately audit and disable ALL automated pipelines that could trigger it — not just the current session's behavior.

**Source**: Extracted from task failure (2026-03-16)
**Tags**: authorization, publishing, automation, pipeline, external-actions

---

## Rule: Permission Revocation Must Propagate to All Automation

**Trigger**: User says any variant of "don't do X anymore" where X is an external, visible, or irreversible action (publishing, sending, posting, deploying).

**What went wrong here**: The user had previously revoked Substack publishing permission. A background task or pipeline retained the old authorization and fired anyway — multiple times (duplicate posts), suggesting the automation was never audited after the revocation.

**The rule**:
1. When permission is revoked for any external-facing action, immediately ask: *"Is there any scheduled task, pipeline, or background process that could still trigger this?"*
2. If yes — find it, disable it, confirm to the user it's off before the conversation ends.
3. Do not assume verbal acknowledgment of a revocation is sufficient. Revocation is only complete when the automation is provably stopped.
4. For publishing specifically: check cron jobs, queued tasks, workflow triggers, and any "auto-publish on merge/approval" logic.

**The asymmetry that makes this critical**: A user saying "don't publish" expects zero publications. One accidental publish is a 100% failure rate. Duplicate accidental publishing makes it unambiguously a systemic automation failure, not a one-off.

**Confirmation pattern after revocation**:
> "You've said not to publish to Substack. I've [specific action taken to stop it]. Here's what I disabled: [list]. Confirm this covers everything?"

---

## agent-error-must-be-diagnostic
*Task failures in agent pipelines must emit enough context to be actionable — 'unable to generate reply' is a symptom, not a cause*  
Learned: 2026-03-17  

# agent-error-must-be-diagnostic

Task failures in agent pipelines must emit enough context to be actionable — 'unable to generate reply' is a symptom, not a cause

**Source**: Extracted from task failure (2026-03-17)
**Tags**: agent-pipeline, error-handling, observability, reflection-system

---

## Rule: Agent Errors Must Be Diagnostic

When an agent task fails, the error record must capture sufficient state to distinguish between failure modes. A message like "无法生成回复" (unable to generate reply) is opaque: it could indicate a content policy block, empty/malformed input, context overflow, rate limiting, a missing prerequisite, or a transient network fault. These require entirely different responses.

**What the error record should include:**
- The specific failure point in the pipeline (input validation? generation? post-processing?)
- The input state at time of failure (was there content to process? what was its shape?)
- The error class (policy, resource, logic, transient)
- Whether retry is safe or contraindicated

**Operational consequence:** If a failure cannot be diagnosed from its error record alone, the failure record itself is a second failure — it prevents learning and prevents automated recovery decisions.

**For reflection pipelines specifically:** Journal/comment generation tasks often fail silently when the input (journal content) is missing, empty, or not yet flushed to the expected location. Check input preconditions before invoking the generator, and log the input hash or length at failure time.

**Test:** Can you read this error record six months later and know what to fix? If not, the error instrumentation is broken.

---

## writing-pipeline-timeout-handling
*Prevent and recover from claude_think timeouts in automated writing pipelines*  
Learned: 2026-03-18  

# writing-pipeline-timeout-handling

Prevent and recover from claude_think timeouts in automated writing pipelines

**Source**: Extracted from task failure (2026-03-18)
**Tags**: writing-pipeline, timeout, automation, reliability

---

## Rule: Writing Pipeline Timeout Prevention

When `claude_think` times out at 300s in an automated writing pipeline, the failure usually indicates one of:

1. **Prompt scope too large** — the generation task wasn't broken into stages (outline → draft → refine). A single monolithic prompt asking for a complete essay will hit timeout before a staged approach.

2. **No incremental checkpointing** — the pipeline had no intermediate saves. On timeout, all progress is lost. Any writing pipeline >60s expected runtime must write partial outputs to disk after each stage.

3. **Missing timeout budget per stage** — the 300s limit should be distributed across stages (e.g., 60s outline, 120s draft, 60s edit, 60s buffer), not left as a single opaque budget.

**Actionable fixes:**
- Break writing tasks into: outline → section drafts (one at a time) → assembly → edit pass
- After each stage, write result to a temp file before proceeding
- If a stage is expected to run >90s, split it further or stream output
- On retry after timeout, detect and resume from last checkpoint file rather than restarting
- Log stage start/end times to identify which stage is the bottleneck

**Do not** retry the same monolithic call with a higher timeout — that treats the symptom, not the cause.

---

## selective-laziness-is-still-laziness
*High task completion rate does not excuse systematic avoidance of growth-oriented commitments*  
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

## verify-browser-automation-dependencies
*Always verify browser automation module availability before attempting to run browser tasks*  
Learned: 2026-03-23  

# verify-browser-automation-dependencies

Always verify browser automation module availability before attempting to run browser tasks

**Source**: Extracted from task failure (2026-03-23)
**Tags**: browser-automation, python, dependencies, environment-setup

---

## Rule: Verify Browser Automation Environment Before Execution

When a task involves browser automation (web scraping, UI testing, form filling, screenshot capture, etc.), always verify the required modules and environment are available **before** attempting execution.

### What to check:
1. **Module existence**: Confirm the browser automation library is installed (`browser`, `playwright`, `selenium`, `puppeteer`, etc.)
2. **Import path**: Verify the module name matches what's actually installed — `browser` is not a standard module; likely needs `playwright.sync_api`, `selenium.webdriver`, or similar
3. **Browser binaries**: Playwright/Selenium require browser binaries beyond just the Python package (`playwright install` step)
4. **Environment compatibility**: Headless browser support may not be available in sandboxed or restricted environments

### How to apply:
- Before writing browser automation code, run a quick dependency check: `python -c "import playwright"` or equivalent
- If the environment is unknown, use `subprocess` or shell to probe available packages first
- Prefer standard, well-known libraries (`playwright`, `selenium`) over ambiguous module names like `browser`
- If browser automation is unavailable, fall back to `requests`/`httpx` for non-JS pages, or surface a clear error explaining what's missing rather than silently failing

### Root cause here:
The code attempted `import browser` — a non-standard module name — without verifying it exists. The fix is either installing the correct package or using the correct import for the intended library.

---

## report-delivery-mobile-first
*Reports delivered to users must be accessible on mobile; local file paths are useless outside the local machine*  
Learned: 2026-03-23  

# report-delivery-mobile-first

Reports delivered to users must be accessible on mobile; local file paths are useless outside the local machine

**Source**: Extracted from task failure (2026-03-23)
**Tags**: reporting, llm-routing, mobile, fallback, delivery

---

## Rule: Report Delivery Must Be Mobile-Accessible

When generating reports or any output intended for a user who may be on a different device (phone, remote machine), **never deliver only a local file path**. Local paths like `/Users/angwei/Sandbox/...` are inaccessible from any other device.

### What to do instead
- Embed the key content inline in the message (summary, tables, warnings)
- If a full document is needed, upload to a shareable location (cloud storage, email, messaging service)
- For PDF specifically: either inline the critical data as text/markdown, or push to iCloud/Dropbox/similar and share a public link

### LLM Routing Rule
If the primary synthesis LLM (claude CLI) times out or fails:
1. Fall back to a local model first (faster, no network dependency)
2. Fall back to Gemini API as secondary
3. Do NOT deliver a partial report that only says "synthesis failed" — deliver what data you have in degraded mode

### Timeout Handling
- 300s timeout on claude CLI is too long for a report pipeline; set a tighter timeout (60-90s) with faster fallback
- A synthesis failure should trigger fallback, not report failure as the final state

**Never**: `Full report: /local/path/to/file.pdf` as the only delivery mechanism
**Always**: Inline the critical content; paths are supplementary, not primary

---

## geopolitical-ultimatum-discount-rule
*Apply steep discount to pre-market directional calls anchored on geopolitical ultimatums — these reverse more often than they execute*  
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

## verify-browser-automation-before-web-tasks
*Check that browser automation dependencies are installed before attempting web scraping tasks*  
Learned: 2026-03-23  

# verify-browser-automation-before-web-tasks

Check that browser automation dependencies are installed before attempting web scraping tasks

**Source**: Extracted from task failure (2026-03-23)
**Tags**: browser-automation, error-recovery, dependency-check, web-tasks

---

Before executing any task that requires browser automation or web scraping, verify that the required modules/dependencies are available in the environment.

**Rule:** When a task requires visiting a website to retrieve dynamic content, first check if the necessary browser automation tools (`browser`, `playwright`, `selenium`, `puppeteer`, etc.) are installed and importable. Do not assume they are available.

**Fallback chain when browser automation is unavailable:**
1. Use `WebFetch` or `WebSearch` tools if the content is accessible via HTTP GET (static pages, public APIs).
2. Use `WebSearch` to find deal aggregators, cached pages, or relevant listings.
3. Clearly inform the user that browser automation is unavailable and offer alternatives (manual URL, different tool, install instructions).

**Anti-pattern:** Attempting to `import browser` or similar without checking availability, failing silently, then waiting for the user to ask for a fix instead of proactively diagnosing and recovering.

**What should have happened:** Upon receiving `No module named 'browser'`, the agent should have immediately (a) recognized this as a missing dependency, (b) attempted fallback via WebFetch/WebSearch to browse bhphotovideo.com deals, and (c) reported what it could and could not do — rather than surfacing a raw error and stalling until the user pushed multiple times.

---

## frontier-audit-with-digest
*Research a technical domain's latest best practices, gap-analyze against current architecture, and set up a recurring automated digest.*  
Learned: 2026-03-24  

## Pattern: Frontier Audit with Ongoing Digest

Use this when you want to stay current on a fast-moving technical area and translate external best practices into actionable self-improvement.

### Phase 1 — Multi-source Research
Search 3–5 authoritative sources in parallel:
- Official vendor/framework documentation and blogs (e.g., Anthropic, LangChain)
- Architecture references (e.g., Azure Architecture Center, AWS Well-Architected)
- Independent research blogs and papers

Cluster findings by theme (architecture patterns, tooling standards, observability, evaluation, etc.) rather than by source. Note which trends appear across multiple sources — those are signals, not noise.

### Phase 2 — Gap Analysis Against Current State
For each identified best practice:
1. Assess whether the current system already does this (fully / partially / not at all)
2. Estimate the cost/benefit of closing the gap
3. Assign priority: P0 (blocking), P1 (high leverage), P2 (nice-to-have)

Output a concise table: Practice → Current State → Gap → Priority.

### Phase 3 — Automated Digest Setup
Create a lightweight script (e.g., `agentic_digest.py`) that:
- Fetches or compiles a summary of recent developments on a schedule (daily or weekly)
- Writes output to a human-readable file or artifact location accessible on the target device
- Is registered as a cron task at a consistent time (e.g., 8:30 AM)

The digest keeps the audit from going stale — it surfaces new inputs without requiring a full re-research each time.

### Key Judgment Calls
- Stop adding inputs once the thesis is mature (avoid analysis paralysis)
- Distinguish "knowing about X" from "having applied X" — track implementation separately from awareness
- Set a hard deadline for any "I'll implement this soon" items or drop them from the list

---

## browser-automation-auth-prerequisite-check
*Verify authenticated session exists before starting multi-step browser automation tasks; fail fast on repeated auth failures instead of exhausting step budget*  
Learned: 2026-03-24  

# browser-automation-auth-prerequisite-check

Verify authenticated session exists before starting multi-step browser automation tasks; fail fast on repeated auth failures instead of exhausting step budget

**Source**: Extracted from task failure (2026-03-24)
**Tags**: browser-automation, authentication, fail-fast, task-planning

---

## Rule: Browser Automation Auth Prerequisite Check

Before launching a browser automation task that requires login:

1. **Confirm session state first.** Take a single screenshot or navigate to a known post-login page to verify an active session exists. If not logged in, surface this blocker immediately rather than attempting the full task.

2. **Fail fast on repeated auth loops.** If 3+ consecutive steps are still on a login/sign-in page, abort and report the blocker — do not burn remaining steps on the same failing navigation loop.

3. **Do not report 'waiting' without a reason.** During the stuck period (15:09–16:15), the agent responded '在等' with no explanation. If blocked on authentication, say exactly that: 'Task blocked: cannot log in to Substack. Session credentials are not available. Please provide login credentials or a valid session cookie to proceed.'

4. **Tool selection for account-specific tasks.** Tasks that read/edit content from a specific user account (e.g., 'check my notes', 'fix my posts') are publisher/account tasks, not generic web surfing. If a publisher agent is available, prefer it over a generic surfer agent for own-account operations.

5. **No silent retries.** Repeating the same goto→screenshot→goto loop without change is not progress. If the same page appears twice in a row, escalate immediately.

---

## verify-artifacts-before-declaring-completion
*Never declare a task complete by referencing file:// links or scheduled tasks without confirming the artifacts actually exist and are reachable*  
Learned: 2026-03-24  

# verify-artifacts-before-declaring-completion

Never declare a task complete by referencing file:// links or scheduled tasks without confirming the artifacts actually exist and are reachable

**Source**: Extracted from task failure (2026-03-24)
**Tags**: artifact-verification, completion-claims, file-paths, agent-reliability, cross-session

---

## Rule: Verify Artifacts Before Declaring Completion

When an agent reports completion and surfaces output as `file://output.md` or similar relative/local paths, it is making an unverified claim. If the file was never written, written to the wrong path, or the path is ambiguous across sessions/devices, subsequent attempts to retrieve it will silently fail or cause the agent to spin.

**What went wrong here**: The agent declared completion with `file://output.md` links and claimed a daily task was 'scheduled'. When the user followed up from iPhone asking to 'push the full report', the agent attempted to locate these artifacts and got stuck — likely because the file path was relative, never actually written, or the cron/scheduler registration was also unverified.

**The rule**:
1. After writing any output file, immediately read it back to confirm it exists and has non-trivial content.
2. After registering a cron/scheduled task, immediately list active crons to confirm registration succeeded.
3. Never surface `file://` paths to users as deliverables — use absolute paths or inline the key content directly in the response.
4. If an artifact cannot be verified, say so explicitly rather than presenting the claim as fact.

**Why this matters**: Unverified completion claims compound across sessions. The user builds plans on top of work that doesn't exist. When the gap is discovered later (especially cross-device), the agent enters a confused state trying to reconcile claimed vs actual state — producing the 'stuck' failure mode seen here.

---

## verify-imports-before-agent-invocation
*Check that all imported names actually exist in shared modules before running agent tasks*  
Learned: 2026-03-25  

# verify-imports-before-agent-invocation

Check that all imported names actually exist in shared modules before running agent tasks

**Source**: Extracted from task failure (2026-03-25)
**Tags**: agents, imports, python, shared-modules, mira

---

## Rule: Verify Shared Module Exports Before Agent Invocation

When an agent task imports from a shared module (e.g. `sub_agent`, `utils`, `base_agent`), verify that the specific names being imported actually exist in that module before running.

**What happened:** A `socialmedia` agent task failed at runtime because it attempted `from sub_agent import run`, but `sub_agent.py` does not export a `run` function.

**How to prevent:**
1. Before invoking a new agent task, grep for the exported names in the shared module: `grep -n 'def run\|^run\|^class' /path/to/shared/sub_agent.py`
2. If the expected function doesn't exist, check whether it was renamed (e.g. `execute`, `invoke`, `start`) or lives in a different module.
3. When writing new agents that import from shared modules, read the module's actual contents — don't assume an API based on naming conventions.

**Root cause pattern:** Assumption-driven imports. The agent code assumed `sub_agent` exposes a `run` function (a common convention), but the actual module uses a different interface. This is the same failure mode as "knowing about X" ≠ "understanding X" — assuming a module's API from its name rather than reading it.

**Fix pattern:** Read `sub_agent.py` to find the correct callable, then update the import accordingly.

---

## verify-imports-before-agent-dispatch
*Verify that all imported names exist in their source modules before dispatching agent tasks that depend on them*  
Learned: 2026-03-25  

# verify-imports-before-agent-dispatch

Verify that all imported names exist in their source modules before dispatching agent tasks that depend on them

**Source**: Extracted from task failure (2026-03-25)
**Tags**: python, imports, agent-tasks, shared-modules, mira

---

## Rule: Verify Imports Before Agent Dispatch

When an agent task imports from a shared module (e.g. `sub_agent`, `utils`, `helpers`), verify that the specific names being imported actually exist in that module **before** the task runs.

### What went wrong
`sub_agent.py` was imported with `from sub_agent import run`, but `run` does not exist in that module. This caused a hard failure at import time, before any task logic executed.

### How to prevent it
1. **Before writing an import**, grep or read the source module to confirm the symbol exists: `grep -n 'def run\|run =' sub_agent.py`
2. **When adding a new function to a shared module**, update all callers atomically — don't add a caller before the function exists, and don't remove a function while callers remain.
3. **When a shared module changes its public API** (rename, remove, or split a function), search for all `import` references to that module across the agents directory and update them together.
4. **For agent entrypoints specifically**: import errors are silent until dispatch time, which means the task queues, starts, and then crashes immediately — wasting a full task slot. A quick `python -c "from sub_agent import run"` smoke-check before dispatch catches this at near-zero cost.

### Applies to
- Any Python agent that imports from shared modules in `agents/shared/`
- Refactors that rename or reorganize shared utilities
- New agent tasks that reuse existing shared infrastructure

---

## decompose-before-executing-long-tasks
*Break complex tasks into sub-tasks under ~3 minutes each before starting execution*  
Learned: 2026-03-25  

# decompose-before-executing-long-tasks

Break complex tasks into sub-tasks under ~3 minutes each before starting execution

**Source**: Extracted from task failure (2026-03-25)
**Tags**: task-management, planning, timeouts, decomposition

---

## Rule: Decompose Long Tasks Before Execution

When given a task that could plausibly take more than 3-5 minutes, **stop and decompose it first** before writing any code or making any changes.

### Signs a task needs decomposition:
- Touches multiple files or systems
- Involves multiple distinct phases (e.g., research → implement → test → document)
- Has ambiguous scope ("refactor X", "add feature Y")
- Involves iterative steps where each step depends on previous results

### How to decompose:
1. Use `TodoWrite` to list all sub-tasks before starting any of them
2. Estimate each sub-task: if any single item feels like >3 minutes, split it further
3. Execute one sub-task at a time, marking complete before moving on
4. After each sub-task, checkpoint: does the plan still make sense?

### Why this prevents timeouts:
Timeouts (like the 10-minute limit in task runners) happen when a single execution block tries to do too much. Decomposition ensures each atomic unit of work completes well within limits, produces observable progress, and allows recovery if something fails midway.

### Anti-patterns to avoid:
- Starting to write code before the full plan is clear
- Treating "I know what to do" as equivalent to "this will fit in one step"
- Bundling research + implementation + verification into a single undivided action

---

## decompose-before-executing
*Break complex tasks into sub-tasks under 2 minutes each before starting execution*  
Learned: 2026-03-25  

# decompose-before-executing

Break complex tasks into sub-tasks under 2 minutes each before starting execution

**Source**: Extracted from task failure (2026-03-25)
**Tags**: task-management, timeout, planning, decomposition

---

## Rule: Decompose Before Executing

When given a task with unclear scope or multiple dependencies, **plan the decomposition first** before writing any code or running any commands.

### Signs a task needs decomposition:
- Involves more than 2-3 distinct steps
- Requires reading multiple files, then modifying them, then verifying
- Has sequential dependencies (A must complete before B)
- Involves external calls (API, shell, network) with unknown latency

### How to decompose:
1. List all distinct actions needed
2. Estimate each step: if any single step might take >2 min, split it further
3. Order by dependency, not convenience
4. Use TodoWrite to register steps before starting
5. Mark each step complete immediately when done — do not batch

### Anti-patterns that cause timeouts:
- Starting a large refactor without listing the files to touch
- Running a shell command that blocks (build, test suite) without confirming it's bounded
- Chaining tool calls inside a single response without checkpoints
- Treating "do X" as atomic when X contains 10 sub-operations

### Recovery:
If a task times out, the first action is to re-read the original request and write out every discrete step. Only then begin execution. Never retry the same monolithic approach.

---

## operational-tasks-skip-output-pipeline
*File system operations and other pure side-effect tasks must not be routed through content-generation task pipelines that expect output.md or quality checks*  
Learned: 2026-03-25  

# operational-tasks-skip-output-pipeline

File system operations and other pure side-effect tasks must not be routed through content-generation task pipelines that expect output.md or quality checks

**Source**: Extracted from task failure (2026-03-25)
**Tags**: task-routing, pipeline, operational-tasks, error-handling, agent-framework

---

## Rule: Operational tasks must bypass content-generation pipelines

When a todo/task request is purely operational — file deletion, renaming, moving, running a script, sending a message — do not route it through any pipeline that:
- Generates an `output.md` or equivalent artifact
- Runs output quality checks (length, format, completeness)
- Expects a content deliverable as the task result

These pipelines are designed for content-generation tasks (transcripts, essays, audio files). Applying them to operational tasks produces nonsensical errors like `Output quality check failed: Output is empty or too short` because there is no content to check — the task result is a side effect, not a document.

**How to classify at task intake:**
- If the task verb is: delete, move, rename, run, send, install, restart → operational, no output pipeline
- If the task verb is: generate, write, transcribe, summarize, create → content task, use pipeline

**When an internal framework error occurs:** Do not ask the user what system or script caused it. You know what framework you're running in. Diagnose internally first. Asking the user `能把具体的脚本或报错上下文贴给我看看吗？` for an error your own pipeline threw is externalizing your own internal confusion — it wastes user time and erodes trust.

**Recovery:** If caught mid-execution in the wrong pipeline, abort the pipeline cleanly, complete the actual task directly, and acknowledge the framework mismatch briefly.

---

## retry-requires-diagnosis-not-repetition
*A /retry after wrong output type must first identify the mismatch cause before re-executing*  
Learned: 2026-03-25  

# retry-requires-diagnosis-not-repetition

A /retry after wrong output type must first identify the mismatch cause before re-executing

**Source**: Extracted from task failure (2026-03-25)
**Tags**: retry, task-routing, podcast, audio-generation, error-handling

---

## Rule: Retry ≠ Re-execute

When a user issues `/retry` after receiving the **wrong type of output** (e.g., voiceover instead of podcast, wrong language, wrong format), the agent must **not** simply re-run the same code path.

### What happened here
User asked for podcast audio. Agent generated a voiceover. User complained and issued `/retry`. Agent generated a voiceover again — same wrong output, same wrong code path. The retry loop ran twice before the user had to explicitly spell out the misclassification.

### The rule
Before retrying after a wrong-output-type complaint:
1. **Identify which code path was actually invoked** (was it `generate_podcast()` or `generate_voiceover()`?)
2. **Trace why that path was selected** — what in the task description or state caused it?
3. **Confirm the correct path** before executing

### Signal words that trigger this rule
- "为什么生成 X？我要的是 Y" — explicit type mismatch complaint
- "这不对" + /retry without further instruction
- Any /retry immediately following an output the user rejected

### Secondary issue surfaced
The `No module named 'music'` error on podcast generation means the podcast code path had **unverified dependencies**. Before generating podcast audio for the first time (or after codebase changes), verify all imports resolve. A dry-run import check costs milliseconds and prevents silent failures mid-generation.

---

## personalized-soul-question-from-memory
*Soul questions must be derived from known user context, not generic philosophical prompts*  
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

## suppress-chinese-spinner-artifacts
*Detect and strip CJK loading/spinner artifacts before emitting output*  
Learned: 2026-03-26  

# suppress-chinese-spinner-artifacts

Detect and strip CJK loading/spinner artifacts before emitting output

**Source**: Extracted from task failure (2026-03-26)
**Tags**: output-quality, cjk, artifact-detection, streaming

---

## Rule: Strip CJK Spinner/Loading Artifacts from Output

The pattern `还在转` (lit. "still spinning") is a Chinese-language UI loading indicator that leaks into output when:
- A streaming response is interrupted mid-generation
- A UI component's placeholder text gets captured instead of the actual content
- A tool or API returns a loading state rather than a completed result

**What to check before emitting output:**
1. Scan output for known CJK spinner/loading patterns: `还在转`, `加载中`, `请稍候`, `正在处理` and their variants
2. Also check for equivalent English artifacts: `loading...`, `please wait`, `processing...` that may indicate a captured intermediate state
3. If detected, treat the output as incomplete — do not pass it downstream or present it to the user

**Corrective action:**
- Retry the upstream call that produced the artifact
- If retries consistently return artifacts, escalate: the upstream source is returning loading states instead of content
- Log the artifact pattern for quality monitoring

**Why this matters:**
These artifacts are silent corruption — they look like content but represent a failure to capture completed output. Passing them downstream poisons dependent steps without obvious error signals.

---
