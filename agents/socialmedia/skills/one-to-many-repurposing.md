# One-to-Many Content Repurposing

**Tags:** substack, writing, social-media, repurposing, distribution

TRIGGER when: user pastes a Substack URL or essay body text and asks for social posts, OR user says they "just published" / "just sent" a newsletter and wants to distribute it, OR user asks for "tweets from this" / "a thread from this" / "LinkedIn version of this."
DO NOT TRIGGER when: user is drafting the original essay, editing for Substack publication, or asking about subscriber growth through non-content means.

## Canonical Asset Rule
The newsletter is the primary artifact — every derivative points back to it, never reproduces it in full.

## Execution Sequence

Given one published essay, execute in order:

### Step 0: Input & Parse
- If user provides a Substack URL: fetch and extract the published essay text.
- If user provides raw essay text: use it directly.
- Confirm the essay is in its final, published form. If it's a draft or outline, stop — this skill is for repurposing finished work.

### Step 1: Decompose the essay into atomic claims
Read the essay and extract every standalone assertion — a sentence or two that makes a defensible point without requiring the surrounding context. A 1,500-word essay typically yields 10–15. List them. Label each: [surprising], [practical], [contrarian], [story]. These labels drive platform selection below.

**Broken if:** You can't state the claim without saying "this means that" or "in the context of" — that's a dependent clause, not an atom. Also broken if every claim gets the same label — re-read for range.

### Step 2: Build the Twitter/X thread (publish same day)
- **Hook:** The essay's single most contrarian or surprising claim, stated as a bare assertion. No hedging, no "I've been thinking about..."
- **Body:** 4–8 numbered tweets. Each tweet = one atomic claim. Cut all transitions, qualifications, and "in other words" restatements. If a claim needs two tweets to land, it's not atomic — split or cut.
- **Closer:** One sentence stating the implication + "Full version in this week's newsletter →" + link.
- **Test before posting:** Read each tweet in isolation. If it doesn't make sense alone, rewrite or cut it.

**Broken if:** The hook is a question (questions get impressions but not clicks), the thread reads like a compressed essay rather than a sequence of independent punches, or tweet 2 starts with "To understand why...' (you're building up instead of delivering).

### Step 3: Extract 3 standalone Twitter/X posts
Pick 3 atomic claims tagged [surprising] or [contrarian]. For each:
- State the claim in ≤ 240 characters
- Add one concrete example or data point NOT used in the thread
- No links. These are freestanding. They create curiosity, not traffic.

**Broken if:** The standalone post requires knowledge of the essay to land, or it's a softened version of a thread tweet rather than a genuinely different framing.

### Step 4: Write the LinkedIn post (publish 48 hours after essay)
- **Open with a specific moment:** "Last Tuesday I [concrete thing that happened]." If the essay doesn't have one, construct a true anecdote that motivated the essay's core insight.
- **Structure:** Situation → what I learned → what I'd tell someone facing this. Short paragraphs (1–3 sentences). Line breaks between every paragraph.
- **Cut:** All nuance, caveats, secondary arguments, and "it depends" qualifications. One clean takeaway.
- **Close with an explicit question** to the reader.
- **Do NOT link to the newsletter in the post body** (LinkedIn suppresses external links). Put the link in the first comment.

**Broken if:** The opening is abstract ("In today's world..."), the post has more than one takeaway, or the closing question is rhetorical rather than genuinely answerable ("What do you think?" is dead — "When did you last face this?" works).

### Step 5 (conditional): LinkedIn carousel
Only if the essay's argument has 4–7 enumerable steps, levels, or categories. Skip otherwise.
- Slide 1: Bold claim or question (no logo slides, no "A thread on...")
- Slides 2–N: One step per slide. Heading + 1–2 sentences max.
- Final slide: Takeaway + newsletter CTA.

**Broken if:** You're forcing a continuous argument into discrete slides — carousels need genuinely separable items, not paragraphs with numbers.

### Step 6 (conditional): Short-form video script
Only if the essay contains a visual metaphor, a before/after comparison, or a concrete demonstration. Skip otherwise.
- Format: Hook (3 sec) → Setup (10 sec) → Payoff (15 sec) → CTA (5 sec)
- Hook must be visual or physical — not "Let me tell you about..."
- Script the exact words. Do not outline.

**Broken if:** The hook is verbal rather than visual, or the script is the LinkedIn post read aloud.

## Decision Gates
| Condition | Action |
|---|---|
| Essay is < 800 words | Skip thread, do 3 standalone posts + LinkedIn only |
| Essay is analytical with no narrative | Skip LinkedIn or find an anecdote to anchor it |
| Essay topic is niche/technical | Skip carousel and video — these need broad appeal |
| Essay was already socially discussed before publishing | Thread first, then skip standalone posts (audience already saw the claims) |