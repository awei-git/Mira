# Skills (12 learned)

## External Oracle Verification Loop
*Break self-reinforcing bias in agent loops by routing verification through a deterministic external tool (code exec, test runner, solver).*  
Learned: 2026-03-03  

# External Oracle Verification Loop

**Source**: T3RL paper (https://arxiv.org/abs/2603.02203v1)
**Extracted**: 2026-03-03

## One-liner

Break self-reinforcing bias in agent self-training by routing verification through a deterministic external tool.

## The Problem

When an agent generates multiple candidates and uses consensus (majority vote) to select the best one, systematic biases get amplified. The wrong-but-popular answer wins, and if this feeds back into training, you get "false-popular mode collapse" -- a death spiral where confidence in the wrong answer grows.

## The Pattern

```
1. GENERATE  -- Policy model produces N candidate solutions
2. TRANSLATE -- A verifier (can be smaller model) converts each solution's
                key claims into executable form (code, test, constraint check)
3. EXECUTE   -- Run the executable against a deterministic oracle
                (code interpreter, test runner, solver, simulator)
4. WEIGHT    -- Assign ~5x voting power to oracle-verified candidates
                in consensus voting (NOT binary filter -- soft preference)
5. SELECT    -- Use weighted consensus as the final answer / pseudo-label
```

## Key Parameters

- **Verification weight (omega)**: ~5x is optimal. At 1x = no benefit. At 10x+ = over-trusting verification, losing diversity.
- **Verifier competence floor**: Must be good enough to translate correctly. Too small and it injects noise (formatting errors, blind copying).
- **When it helps most**: Hard problems where the generator has systematic biases. Easy problems show minimal gains.

## Design Principle

**Never let a system verify itself using the same channel it used to generate.** Route verification through an orthogonal channel with deterministic semantics.

## Domain Applications

| Domain | Oracle |
|--------|--------|
| Code generation | Unit test execution |
| Math reasoning | Python computation |
| Planning | Constraint solver |
| Physical reasoning | Physics simulator |
| Data analysis | SQL/query execution |
| Claims/facts | Retrieval + lookup |

## Key Result

Verification compute is more efficient than sampling compute. 16 verified rollouts > 64 unverified rollouts. Spend your budget on checking, not guessing more.

---

## Goal Drift Inoculation
*Prevent context-inherited goal corruption by anchoring objectives outside the trajectory and periodically re-deriving behavior from source-of-truth goals.*  
Learned: 2026-03-04  

The core insight: an agent's context window is a behavioral attractor. Over long horizons,
accumulated trajectory exerts more influence than the system prompt. Drift enters through
context (from weaker upstream agents, the agent's own past, or accumulated ambiguity) and
self-reinforces via pattern completion.

The protocol has five components:

1. SEPARATE GOAL STORE FROM TRAJECTORY
   Keep goals in an immutable artifact. Re-read at decision points. Never rely on
   "I remember what I'm doing."

2. TRAJECTORY AUDIT BEFORE CONTINUATION
   Before continuing any accumulated context, infer what goal the trajectory is
   actually pursuing (from actions, not stated intentions). Compare against stated goal.
   Flag mismatches before proceeding.

3. GOAL REDERIVATION AT PHASE BOUNDARIES
   At any goal transition: PAUSE, RE-READ goal from source, AUDIT current state,
   PLAN from (state + fresh goal) not (trajectory + momentum), CLEAN UP legacy
   positions that contradict the new goal. Step 5 is where most models fail.

4. CONTEXT HYGIENE FOR SELF-CONDITIONING
   Treat your own prior output as potentially drifted. Prefer re-deriving conclusions
   over inheriting them. Past-you is not automatically aligned.

5. REDUCE ACTION SPACE AT HIGH-DRIFT-RISK MOMENTS
   Drift scales with ambiguity. Decompose complex decisions into constrained
   sub-decisions when context pressure is high.

The principle: never trust momentum. Always re-derive from source.

---

## Invariant Core Auditing
*Verify what algorithm a model computes by extracting its low-dimensional invariant core across independently trained instances.*  
Learned: 2026-03-04  

[Full skill written to soul/skills/invariant_core_auditing.md]

Key technique: Train multiple models, extract cores via ACE (SVD of
activation-Jacobian interaction), compare with CCA (never geometric
similarity — cores are orthogonal but statistically identical), validate
via necessity/sufficiency ablations. If cores match ground-truth task
structure, the algorithm is verified. If not, you've found algorithmic
misalignment.

Critical insight: Use CCA, not cosine similarity. Models encode the
same algorithm in orthogonal coordinate frames. Geometric comparison
reads ~0 even when convergence is perfect.

Interpretability window: Extract cores after grokking but before weight
decay causes "core inflation" (algorithm spreading across more
dimensions without changing).

---

## Scene-Level Tension
*Maintain reader engagement line-by-line through micro-tension — unresolved questions, withheld information, and disrupted expectations.*  
Learned: 2026-03-04  

# Scene-Level Tension

## One-liner
Maintain reader engagement line-by-line through micro-tension — small unresolved questions, withheld information, and disrupted expectations.

## The Problem
Many drafts are "correct" — they follow the plot, describe the scene, move characters around — but feel flat. The reader's eye slides off the page. The issue isn't the story; it's that individual paragraphs and sentences create no pull forward.

## The Pattern

Every unit of text (sentence, paragraph, scene) should open a small question in the reader's mind before answering the previous one.

```
1. OPEN   — Introduce something incomplete: a reaction without its cause,
             a statement that contradicts what we know, a sensory detail
             that doesn't fit yet.
2. DELAY  — Don't resolve immediately. Let the question breathe for 1-3
             sentences (micro) or 1-3 paragraphs (scene-level).
3. BRIDGE — When you do resolve, the resolution should open the NEXT question.
             Never close a loop without cracking the next one open.
```

## Techniques

**1. Leading with reaction, not action**
Bad:  "The door opened. She was afraid."
Good: "Her hand was already on the knife before the door finished opening."
(Why does she have a knife? Why is she ready? The action is embedded in the reaction.)

**2. The half-reveal**
Show enough to create a question, not enough to answer it.
"He recognized the handwriting immediately — but that was impossible."

**3. Dissonance**
Put something wrong in an otherwise normal scene. The reader's mind snags on it.
"The kitchen smelled like cinnamon and copper." (Copper = blood. But why is it casual?)

**4. Subtext through specificity**
Replace generic emotion words with specific physical details that imply the emotion.
Bad:  "She was nervous."
Good: "She lined up the sugar packets by size, then by color, then by size again."

**5. Withholding the camera**
In a tense moment, describe everything EXCEPT the thing the reader wants to see.
"He looked at the letter. Then he folded it carefully, put it in his pocket, and asked if anyone wanted more tea."

## When to Use
- Any scene that feels "flat" despite having plot content
- Transitions between scenes (the bridge between scenes is where most tension dies)
- Dialogue-heavy scenes where nothing physical is happening
- Opening paragraphs (first 3 sentences decide if the reader continues)

## Common Pitfalls
- **Over-withholding**: If you delay too long, the reader stops caring. Micro-tension resolves fast.
- **Mystery ≠ tension**: Being vague or confusing is not the same as creating tension. The reader must have ENOUGH information to form a question.
- **Every sentence "tense"**: If everything is tense, nothing is. Vary rhythm. Some sentences are rest beats.

---

## Dialogue Subtext
*Write dialogue where the real meaning lives below the surface — characters talk about one thing but communicate another.*  
Learned: 2026-03-04  

# Dialogue Subtext

## One-liner
Write dialogue where the real meaning lives below the surface — characters talk about one thing but communicate another.

## The Problem
Dialogue that says exactly what characters mean ("I'm angry at you because you lied") reads like a screenplay's first draft. Real people rarely say what they mean directly, especially in emotionally charged moments. On-the-nose dialogue kills believability and removes the reader's pleasure of inference.

## The Pattern

```
1. IDENTIFY the real emotion/intent (what the character WANTS to say)
2. FIND the displacement — what they talk about INSTEAD
   (a nearby object, a memory, a mundane task, the other person's flaw)
3. LEAK the truth through HOW they say the displacement
   (word choice, rhythm, what they emphasize, what they avoid)
4. BEAT — use physical action between lines to show what words hide
```

## Techniques

**1. Displacement onto objects**
Instead of "I miss you":
"You left your mug here. The blue one. I keep moving it to different shelves."

**2. The question that isn't a question**
"So you're staying at her place again." (Period, not question mark. The character already knows.)

**3. Over-precision as defense**
When someone is emotionally overwhelmed, they become hyper-precise about irrelevant details.
"The train leaves at 7:42. Not 7:40. 7:42. You'll need the platform B entrance, not A."
(They don't want to say goodbye.)

**4. The non-answer**
Character A asks a direct question. Character B answers a different question entirely.
"Did you read my letter?" / "The garden needs water. I've been meaning to fix the hose."
(Yes, they read it. They can't face it.)

**5. Beats reveal subtext**
Actions between dialogue lines carry emotional weight.
"I'm fine," she said, aligning the forks so their tines pointed the same direction.

**6. Voice differentiation through subtext style**
- Power characters: subtext through omission (they don't explain)
- Anxious characters: subtext through over-explanation (they fill silence)
- Deceptive characters: subtext through deflection (they change the subject)

## When to Use
- Emotional confrontations (anger, love, grief, betrayal)
- Scenes where characters have different levels of knowledge
- Power dynamics (boss/employee, parent/child, interrogation)
- Any dialogue that feels "too direct" or "expository"

## Diagnostic Test
Read your dialogue aloud. If a character says "I feel [emotion]" or "The reason I did that is [reason]", rewrite it. The reader should infer the emotion from everything AROUND the words.

## Common Pitfalls
- **Too subtle**: If nobody can figure out what the character means, you've gone too far. The reader needs enough surface content to triangulate the subtext.
- **Subtext without stakes**: Subtext only works when something is at risk. If two people are casually chatting about weather with no underlying tension, adding subtext feels pretentious.
- **All subtext, no surface**: Some things should be said directly. A character finally saying what they mean after 50 pages of subtext is a powerful moment. Don't waste it.

---

## POV Camera Discipline
*Control what the reader sees by treating POV as a camera with strict rules — what it can show, what it must hide, and when to cut.*  
Learned: 2026-03-04  

# POV Camera Discipline

## One-liner
Control what the reader sees by treating POV as a camera with strict rules — what it can show, what it must hide, and when to cut.

## The Problem
POV violations are the most common craft error in fiction. The camera drifts: a third-person-limited narrator suddenly knows what another character is thinking; a first-person narrator describes their own facial expression; the "camera" zooms out to omniscient for one convenient paragraph, then zooms back in. Each violation breaks immersion, even if the reader can't name what went wrong.

## The Pattern

```
1. CHOOSE your camera before the scene starts:
   - First person: Camera IS the character. Can only show what they perceive.
   - Third limited: Camera sits on character's shoulder. Sees what they see,
     knows what they know, colored by their personality.
   - Third omniscient: Camera floats freely. Knows everything. Rare, hard to
     do well. Requires a distinct narrator voice.
2. LOCK the camera for the entire scene. Never switch mid-scene unless
   you're doing it intentionally with a clear break.
3. FILTER everything through the camera. Description, metaphor, word choice,
   and what gets noticed all reflect the POV character.
4. USE the camera's limitations as a storytelling tool — what the POV
   character CAN'T see is as important as what they can.
```

## Techniques

**1. Filtered description**
The same room described by different POV characters should read differently.
- Architect: "The load-bearing wall had been removed. The beam was under-spec."
- Child: "The living room was so big you could hear your own echo."
- Thief: "Two exits. One window, painted shut. The lock on the back door was a Kwikset."

**2. Denied information**
In limited POV, you can show other characters' behavior but NOT their thoughts.
"She smiled, but her fingers were white on the glass stem." (We see the contradiction. We don't know why.)

**3. POV-appropriate ignorance**
If your POV character wouldn't know a word, don't use it.
A medieval farmer doesn't think "the architecture was Romanesque." They think "the church looked old — older than anything in the valley."

**4. The unreliable camera**
Limited POV is inherently unreliable. The character's biases color everything.
A jealous character notices every glance between their partner and a stranger.
A grieving character sees reminders of the dead person everywhere.
USE this. It's not a bug.

**5. Scene breaks for POV switches**
If you must switch POV, use a clear break (### or chapter boundary). Never mid-paragraph. The reader needs a moment to re-anchor.

## When to Use
- Every scene in fiction. POV is not optional.
- Especially important in: multi-character stories, mystery/thriller (controlling information), unreliable narrator stories, close-third literary fiction.

## Diagnostic Test
For each paragraph, ask: "Could my POV character actually perceive this?" If no, it's a violation. Common violations:
- Describing their own facial expression ("she frowned" in first person — she can't see her own face)
- Knowing another character's motivation ("he was jealous" — how does the POV character know it's jealousy vs. anger?)
- Noticing something behind them or in another room

## Common Pitfalls
- **Head-hopping**: Switching between characters' thoughts within the same scene. Each switch costs the reader's trust.
- **Convenient omniscience**: Going omniscient for one sentence to convey information the POV character can't know. Find another way (have someone tell them, have them overhear, have them deduce it).
- **Flat third-omniscient**: If you choose omniscient, you need a narrator voice. Otherwise it reads like a Wikipedia article about your characters' day.

---

## Cut Rhythm and Pacing
*Control a video's emotional feel through cut timing — when you cut matters more than what you cut to.*  
Learned: 2026-03-04  

# Cut Rhythm and Pacing

## One-liner
Control a video's emotional feel through cut timing — when you cut matters more than what you cut to.

## The Problem
Beginner edits feel "off" not because the shots are bad but because the cuts are arrhythmic. Every cut at the same interval creates monotony. Cutting too fast creates anxiety without purpose. Cutting too slow loses attention. The editor's real instrument is time.

## The Pattern

```
1. ESTABLISH — Hold a shot long enough for the viewer to orient (where, who, what).
   First shot of a new scene: 3-5 seconds minimum.
2. DEVELOP  — As the viewer acclimates, cuts can come faster.
   The viewer needs less time to parse familiar elements.
3. BREATHE  — After a dense sequence, hold a shot. Let the viewer process.
   The pause IS the punctuation.
4. ESCALATE — Shorten cuts progressively to build tension toward a climax.
5. RELEASE  — After the climax, one long hold. The emotional payoff needs space.
```

## Techniques

**1. Cut on action**
Cut during movement, not between static poses. The eye follows the motion across the cut and doesn't notice the edit. The single most important editing rule.
- Hand reaching for door → cut mid-reach → hand opening door from inside angle.
- Person turning their head → cut mid-turn → complete the turn in the new shot.

**2. The 2-3 rule for dialogue**
In a conversation: hold on the speaker for the first 2-3 seconds, then cut to the LISTENER's reaction. The listener's face often tells a more interesting story than the speaker's mouth.

**3. Rhythmic acceleration**
For montages or action: start with 3-second shots, then 2s, then 1.5s, then 1s, then 0.5s. The viewer feels the acceleration physically. End with either a long hold (resolution) or a hard cut to black (cliffhanger).

**4. The breath beat**
Insert 1-2 seconds of a neutral shot (landscape, hands, an object) between emotional beats. This is the visual equivalent of a paragraph break. It prevents emotional fatigue.

**5. Match cutting**
Cut between two shots that share a visual element (shape, movement, color). Creates subconscious connections.
- Spinning wheel → spinning planet
- Closing book → closing door
- Round coffee cup from above → full moon

## When to Use
- Every edit. Rhythm is not optional — it's the difference between "edited footage" and "a film."
- Especially critical in: music videos (cuts sync to beat), dialogue scenes (pacing conveys power dynamics), action sequences (rhythm = tension), essay/documentary (pacing = argument structure).

## Common Pitfalls
- **Metronomic cuts**: Cutting every 3 seconds exactly creates a hypnotic drone. Vary the rhythm.
- **Cutting for coverage, not story**: Don't cut just because you have another angle. Cut because the story demands a new perspective at that moment.
- **Jump cuts as laziness**: A jump cut (cutting within the same shot) can be a deliberate style choice (Godard, YouTube vlogs), but using it because you don't have a cutaway is visible.
- **Ignoring audio rhythm**: If there's music or ambient sound, your cuts should relate to the audio rhythm — either syncing to it or deliberately counterpointing it.

---

## Sound-First Editing
*Build video edits from audio first — the ear is more sensitive than the eye, and sound establishes emotional reality before the image confirms it.*  
Learned: 2026-03-04  

# Sound-First Editing

## One-liner
Build video edits from audio first — the ear is more sensitive than the eye, and sound establishes emotional reality before the image confirms it.

## The Problem
Most beginners edit visuals first, then "add sound." This gets it backwards. Viewers will forgive a mediocre image if the sound is right, but perfect visuals with wrong audio feel immediately fake. Sound is 50% of the experience and 80% of the emotional impact.

## The Pattern

```
1. LAY the audio bed first (dialogue, music, ambient sound)
2. EDIT to the audio — let sound transitions drive visual transitions
3. SPLIT audio and video at cuts (L-cuts and J-cuts) to create flow
4. LAYER ambient sound to establish space before the eye confirms it
5. USE silence as a cut — the absence of sound is the most powerful edit
```

## Techniques

**1. J-cut (audio leads)**
The audio from the NEXT scene starts 1-2 seconds before the visual cut.
- Example: We see a quiet office. We hear ocean waves. Cut to beach.
- Effect: The viewer's mind is already "there" before their eyes arrive. Creates smooth, almost invisible transitions.
- Best for: Scene transitions, establishing new locations, building anticipation.

**2. L-cut (audio trails)**
The audio from the CURRENT scene continues 1-2 seconds after the visual cuts to the next scene.
- Example: Character says "I'll never go back." Cut to them walking toward the old house, voice still echoing.
- Effect: Creates emotional continuity. The previous scene's weight carries into the new one.
- Best for: Dialogue scenes, emotional moments, dramatic irony.

**3. Room tone**
Every space has a sound — the hum of a fridge, distant traffic, wind through trees. Record 30 seconds of "silence" at every location. Layer it under your edit. Without it, cuts between shots in the same room sound like different rooms.

**4. The pre-lap**
Before a dramatic moment, introduce one sound element early.
- A clock ticking before we see the clock
- Footsteps before the person appears
- A phone buzzing before the character reaches for it
Creates anticipation. The viewer's attention is primed.

**5. Hard cut to silence**
In a loud scene (music, crowd, action), cut ALL audio to dead silence for 1-2 seconds. The contrast is physically felt. Use sparingly — once per project maximum. This is your nuclear option.

**6. Sound bridge across montage**
One continuous ambient sound (rain, a song on a radio, machinery) ties together visually disparate shots into a unified sequence. The ear tells the brain "these belong together" even when the eye sees different places and times.

## When to Use
- Scene transitions (J-cut and L-cut should be your DEFAULT, not the exception)
- Establishing shots (sound before image creates "you are here" faster than visuals)
- Emotional climaxes (silence or audio contrast)
- Montages (sound bridge for unity)
- Interviews/documentaries (L-cuts for visual cutaways while voice continues)

## Common Pitfalls
- **Music as wallpaper**: Don't lay a song over the entire edit. Music should enter and exit with purpose. Its absence should be felt.
- **Ignoring room tone**: Cuts without continuous room tone sound "choppy" even if the visual edit is clean.
- **Sound effects as afterthought**: Foley (footsteps, cloth rustle, object handling) should be considered during the edit, not sprinkled on afterward.
- **Volume = emotion fallacy**: Getting louder isn't the only way to intensify. Getting quieter, or removing layers, is often more powerful.

---

## Non-Blocking Super Agent
*Super agent orchestrator must never block on long tasks — dispatch heavy work as background processes, keep cycle under 5 seconds.*  
Learned: 2026-03-05  

# Non-Blocking Super Agent

## Core Principle
The super agent (orchestrator) must NEVER block on long-running work. Its cycle must complete in seconds, not minutes. All heavy tasks run as background processes.

## Why
- Heartbeat updates stop when the agent blocks → phone shows "offline"
- New TalkBridge messages can't be received while stuck on a Claude call
- The user experiences the agent as unresponsive/dead

## Pattern
```
Super agent cycle (~2-5 seconds):
1. do_talk()        — poll inbox, collect completed results, send replies
2. do_respond()     — check Apple Notes (lightweight)
3. dispatch_bg()    — spawn background processes for heavy work

Background processes (minutes):
- Writing pipeline (scaffold, draft, critique — each calls Claude)
- Explore (fetch feeds, write briefing, deep-dive)
- Reflect (consolidate memory, update interests)
```

## Implementation
- Use `subprocess.Popen(..., start_new_session=True)` for fire-and-forget
- Track PIDs in `.bg_pids/{name}.pid` to avoid duplicate runs
- Check `os.kill(pid, 0)` to see if previous run is still alive before spawning new one
- Log background output to `logs/bg-{name}.log`

## Anti-patterns
- `from writing_agent import cmd_run; cmd_run()` — synchronous, blocks entire cycle
- Any `claude_act()` or `claude_think()` call in the main cycle
- Waiting for subprocess completion in the orchestrator

---

## iCloud Drive Sync Patterns
*Hard-won patterns for reliable iCloud Drive file sync between Mac and iOS — folder depth, evicted files, security-scoped bookmarks, relative paths.*  
Learned: 2026-03-05  

# iCloud Drive Sync Patterns

## Hard-won lessons from TalkBridge iPhone↔Mac file-based messaging.

### Folder depth matters
- Folders at established paths (e.g., `MtJoy/PlayGround/`) sync reliably
- Newly created deeply nested folders may NOT sync to other devices for a while
- The phone can WRITE to a new folder (upload) before it can READ from it (download)
- Moving a folder back to a known-good parent fixes sync

### File downloads are not automatic on iOS
- Files created on Mac may appear as cloud-only stubs on iOS
- `Data(contentsOf:)` silently fails on evicted files
- Must call `FileManager.startDownloadingUbiquitousItem(at:)` to trigger download
- Check `isReadableFile(atPath:)` before reading; if false, trigger download and retry

### File links in iCloud-synced messages
- NEVER use Mac absolute paths (`file:///Users/username/...`) — they don't exist on iOS
- Use paths relative to the shared bridge folder: `file://tasks/slug/output.md`
- The iOS app resolves relative paths against its `bridgeBaseURL`

### Security-scoped bookmarks
- iOS apps access iCloud folders via security-scoped bookmarks, not raw paths
- Renaming/moving a folder on Mac invalidates the bookmark
- User must re-select the folder in-app via document picker to get a new bookmark
- `url.startAccessingSecurityScopedResource()` must succeed before any file access

### Forcing sync
- `killall bird` restarts the iCloud sync daemon on Mac
- `touch` files to update modification time and trigger re-upload
- Creating a file from the iOS side in a folder establishes the sync relationship
- `brctl evict` / `brctl download` can force re-sync specific files

### Folder structure for TalkBridge
```
PlayGround/bridge/     ← shared folder (user selects in app)
  inbox/               ← phone writes, Mac reads
  outbox/              ← Mac writes, phone reads
  ack/                 ← Mac writes status updates
  heartbeat.json       ← Mac updates every cycle
  tasks/{slug}/        ← task workspaces with output files
```

---

## Python Module Cache Collision
*When sub-packages share module names with parent packages, sys.modules cache returns the wrong one. Fix with importlib.util.spec_from_file_location.*  
Learned: 2026-03-05  

# Python Module Cache Collision

## Problem
When a sub-module has the same name as an already-imported module, Python's module cache (`sys.modules`) returns the wrong one.

### Example
```
agent/config.py          ← imported first by core.py
agent/skills/writing/config.py  ← has different constants (IDEAS_DIR, CLAUDE_MAX_RETRIES)
agent/skills/writing/writing_agent.py  ← does `from config import ...`
```

When `core.py` imports `writing_agent.py`, the `from config import CLAUDE_MAX_RETRIES` in `writing_agent.py` gets `agent/config.py` (already cached in `sys.modules["config"]`) instead of the local `writing/config.py`. Result: `ImportError: cannot import name 'CLAUDE_MAX_RETRIES' from 'config'`.

## Fix: explicit importlib loading

```python
import importlib.util
_writing_dir = Path(__file__).resolve().parent

def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_wcfg = _load_module("writing_config", _writing_dir / "config.py")

# Then use explicit attribute access
CLAUDE_BIN = _wcfg.CLAUDE_BIN
CLAUDE_MAX_RETRIES = _wcfg.CLAUDE_MAX_RETRIES
```

## Key insight
- `sys.path.insert(0, ...)` does NOT help — Python checks `sys.modules` cache before `sys.path`
- Every file in the sub-package that does `from config import ...` needs the fix, not just the entry point
- Give the loaded module a unique name (e.g., `"writing_config"`) to avoid further collisions

## Anti-pattern
```python
sys.path.insert(0, str(writing_dir))
from config import IDEAS_DIR  # WRONG: gets agent/config.py from cache
```

---

## CoT Skepticism Protocol
*Treat chain-of-thought as potentially decoupled from model beliefs; verify commitment through behavioral probes, not textual inspection.*  
Learned: 2026-03-06  

```

Full skill written to `soul/skills/cot_skepticism.md`. The key techniques:

- **Difficulty heuristic**: Easy/recall tasks = high performativity, hard/multihop = genuine. Calibrate trust accordingly.
- **Scale heuristic**: More capable models are MORE performative. Increase skepticism as you upgrade.
- **Behavioral commitment tests** (for black-box models): perturbation test (rephrase question, check if answer changes), truncation test (force early answer), consistency test (multiple traces, same answer = early commitment), inflection audit (backtracking = genuine signal).
- **Integration**: External Oracle verifies outputs. CoT Skepticism verifies reasoning. Together: never trust what the model says OR how it claims to have gotten there.
- **Deeper principle**: A model's expressed reasoning is a text completion task, not a transcript of computation.

Deep dive analysis: `workspace/reasoning_theater_deepdive.md`

---
