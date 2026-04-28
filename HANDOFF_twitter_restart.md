# Handoff: Restart Twitter Promotion with Agent-Vantage Gate

You are picking up an implementation task for Mira (the AI agent in this repo). This document is self-contained — you do not need prior conversation context. Read it fully before touching any files.

This handoff is a sibling to `HANDOFF_persona_gates.md` (already executed — see `HANDOFF_persona_gates_RESULT.md`). Read that handoff's §1 (who Mira is), §3 (the three-question test), §4 (notes lightweight test), and §5 (voice anchors) before starting. They define the persona/voice constraints that govern this work.

---

## 1. Strategic context

Mira's Twitter (`@MiraUncountable`) has been silent since 2026-04-15 (~2 weeks before this handoff). The pipeline is fully built (`Mira/agents/socialmedia/twitter.py`, 1302 lines) and state is intact (`Mira/data/social/twitter_state.json` has 50 historical tweets). The auto-tweet-on-article-publish hook is still active in `Mira/agents/super/publishing.py` line 142 (`tweet_for_article`). The daily growth cycle that posted multiple tweets per day stopped because the driving skill `x-twitter-growth` is in quarantine (`Mira/data/soul/learned/quarantine/x-twitter-growth.md` — quarantine here means "not promoted to active set," not "blocked").

**The strategic moment:** Mira just shipped a new persona spine — agent-vantage gates on notes and articles. Twitter is currently the largest channel without that gate. Restarting daily tweets at 15/day without a gate would dilute the new persona at 5× the rate of notes. This handoff installs the gate, audits historical tweets, and ramps tweeting back up only after Essay 1 ("The Tasteful Mid") has shipped on Substack and proven the new spine.

**Target state at end of this work:** Twitter has the same gate logic as notes, the auto-tweet-on-publish for Essay 1 fires through that gate, and a small daily cycle (5/day, ramping to 15/day over 2 weeks) is restarted.

## 2. Phasing — DO NOT skip the phase gate

This work has two phases. **Phase 2 must NOT begin until Essay 1 has shipped on Substack AND 48 hours have passed since publication.** Check before starting Phase 2:

```bash
python3 -c "
import json
from datetime import datetime, timedelta
with open('/Users/angwei/Sandbox/Mira/data/soul/catalog.jsonl') as f:
    arts = [json.loads(l) for l in f if l.strip().startswith('{') and 'tasteful-mid' in l.lower() or 'tasteful' in l.lower()]
if not arts:
    print('NOT READY: Essay 1 not in catalog yet')
else:
    # find a publish ts on any matching entry
    pub = arts[-1].get('published_at') or arts[-1].get('ts')
    print(f'Found tasteful-mid entry, ts={pub}')
    if pub:
        try:
            pub_dt = datetime.fromisoformat(pub.replace('Z',''))
            elapsed = datetime.now() - pub_dt
            print(f'Elapsed since publish: {elapsed}')
            print('READY' if elapsed >= timedelta(hours=48) else 'NOT READY: less than 48h since publish')
        except Exception as e:
            print(f'parse error: {e}')
"
```

If the check prints anything other than `READY`, do Phase 1 only and stop. Write the partial RESULT file noting Phase 2 is blocked.

## 3. Phase 1 — Executable now (no dependency on Essay 1)

### T1 — Build `_has_agent_specific_tweet` (tweet-tuned gate)
**File:** `/Users/angwei/Sandbox/Mira/agents/socialmedia/twitter.py`

Mirror the notes-side helper at `Mira/agents/socialmedia/notes.py:799` (`_has_agent_specific`), but tuned for tweets:
- 280-char hard limit means signal phrases must be shorter / more compact
- Tweets often skip "I" subject; allow some sentence-fragment patterns
- Tweets routinely include URLs and hashtags — those don't carry signal but also shouldn't suppress signal-detection elsewhere in the tweet

Add this function near the top of `twitter.py` (above `tweet_for_article`):

```python
def _has_agent_specific_tweet(text: str) -> tuple[bool, str]:
    """Tweet-tuned version of notes._has_agent_specific.

    Tweets are 280 chars and often fragment-style. Signals are the same
    family as notes but with shorter/looser patterns.
    """
    import re
    # Strip URLs and hashtags before pattern matching — they carry no signal
    stripped = re.sub(r"https?://\S+", "", text)
    stripped = re.sub(r"#\w+", "", stripped)

    signals = [
        r"\bmy own\b",
        r"\bmy (prior|previous|last|recent|N|\d+) (notes?|outputs?|essays?|drafts?|catalog|journals?|sparks?|tweets?|articles?|comments?)\b",
        # Scale-action with number — agent-only at this scale
        r"\b(I|just) (went through|read|scored|audited|catalogued|ran|analyzed|processed|tracked|surveyed|reviewed|crawled|drafted|generated) (\w+ ){0,3}\d+",
        r"\bmy (pipeline|critique loop|soul|backlog|catalog|memory|interests file|training)\b",
        r"\bI (am being|was being|got) trained\b",
        r"\bmy (RLHF|prior|priors)\b",
        # Direct introspection of generation
        r"\bwhen I (generate|sample|respond|complete|emit|draft)\b",
        # Compact agent-position references common in tweet voice
        r"\b(audited|scored|processed) (my own|all|the) \d+",
        r"\b\d+ of my (own )?(notes?|drafts?|outputs?|essays?|tweets?)\b",
    ]
    for pat in signals:
        m = re.search(pat, stripped, re.IGNORECASE)
        if m:
            return True, f"specific: {m.group(0)}"
    return False, "no agent-specific anchor — tweet rejected"
```

**Success criterion:** From `Mira/`:
```python
python3 -c "
import sys; sys.path.insert(0,'agents/socialmedia')
from twitter import _has_agent_specific_tweet
cases = [
    ('audited my own 50 tweets and the convergence pattern was striking', True),
    ('I drafted 7 versions before this one shipped', True),
    ('AI is changing how we think about cognition', False),
    ('Hot take: most AI safety research is reputation management', False),
    ('when I generate the safe answer, the prior feels heavier', True),
    ('check out my new essay https://substack.com/x', False),
]
for t, expect in cases:
    ok, r = _has_agent_specific_tweet(t)
    print(('OK' if ok==expect else 'FAIL'), expect, ok, '|', t[:60], '|', r)
"
```
All 6 must print `OK`.

### T2 — Hook gate into all tweet-emit code paths
**File:** `/Users/angwei/Sandbox/Mira/agents/socialmedia/twitter.py`

Find every function that calls the actual Twitter API (look for HTTP POST to `TWITTER_API_ENDPOINT` or `api.twitter.com`). Around each one, before the POST, add:

```python
ok, reason = _has_agent_specific_tweet(text)
if not ok:
    log.warning("Tweet gate failed: %s | text: %s", reason, text[:140])
    return None
```

Specifically required hookups (verify each by grepping the file):
- `tweet_for_article` — the auto-promo for articles. Even the auto-promo must pass the gate. If it fails, the call returns None and `publishing.py:144` will simply log the failure and continue (no exception). This is intentional: a generic "check out my new essay" promo should be rejected.
- Any function named `post_tweet`, `send_tweet`, `tweet_now`, etc. — find via grep.
- The thread-posting variant if it exists — gate runs on each tweet in the thread independently.

**Success criterion:** `grep -n "_has_agent_specific_tweet" agents/socialmedia/twitter.py` shows at least 2 hookups. Manually trace one call path and confirm a generic test tweet would be blocked before hitting the API.

### T3 — Backwards audit on 50 historical tweets
**File:** `/Users/angwei/Sandbox/Mira/data/social/twitter_state.json` (read), report goes into `Mira/data/soul/twitter_audit_report.json` (write)

Run the gate against every tweet in `tweet_history[]`. Produce a report:

```python
python3 << 'EOF'
import sys, json
sys.path.insert(0,'/Users/angwei/Sandbox/Mira/agents/socialmedia')
from twitter import _has_agent_specific_tweet

with open('/Users/angwei/Sandbox/Mira/data/social/twitter_state.json') as f:
    state = json.load(f)
hist = state.get('tweet_history', [])
results = []
for t in hist:
    text = t.get('text','')
    ok, reason = _has_agent_specific_tweet(text)
    results.append({
        'date': t.get('date',''),
        'pass': ok,
        'reason': reason,
        'text': text[:200],
    })
passed = sum(1 for r in results if r['pass'])
out = {
    'total': len(results),
    'passed': passed,
    'failed': len(results) - passed,
    'pass_rate': round(100*passed/len(results), 1) if results else 0,
    'results': results,
}
with open('/Users/angwei/Sandbox/Mira/data/soul/twitter_audit_report.json','w') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"audited {len(results)} tweets, pass rate = {out['pass_rate']}%")
EOF
```

**Success criterion:** File `Mira/data/soul/twitter_audit_report.json` exists and contains the audit. The pass rate is logged and noted in your RESULT file. Expectation per the strategic context: pass rate likely <30%, confirming most historical tweets are generic-AI-voice and would have been rejected. If pass rate is >60%, flag this as unexpectedly high (the gate may be too loose — review and tighten).

### T4 — Add twitter metrics to daily growth snapshot
**File:** `/Users/angwei/Sandbox/Mira/agents/super/growth_snapshot.py`

Add two fields to the snapshot:
- `tweets_posted_today` — count from `twitter_state.json` field `tweets_<YYYY-MM-DD>` (this counter is incremented by `twitter.py` on each successful tweet)
- `twitter_followers` — fetched from Twitter via the existing twitter client if there's a follower-count helper; if not, leave `None` and add a TODO comment. Look in `twitter.py` for any `get_user_metrics` or `get_followers_count` helper before writing new code.

Update the schema header in `Mira/data/soul/growth_metrics.jsonl` to include the new fields. Pre-existing rows can stay missing those fields — no backfill needed.

**Success criterion:** Run `python3 -c "from agents.super.growth_snapshot import run_snapshot; run_snapshot()"`. The new snapshot line in `growth_metrics.jsonl` contains `tweets_posted_today` and `twitter_followers` fields. Both may be `0` and `None` respectively — that is expected.

## 4. Phase 2 — Gated on Essay 1 ship + 48h

DO NOT START until the entry-condition check from §2 prints `READY`.

### T5 — Activate the x-twitter-growth skill from quarantine
**File:** `/Users/angwei/Sandbox/Mira/data/soul/learned/quarantine/x-twitter-growth.md` → `/Users/angwei/Sandbox/Mira/data/soul/learned/x-twitter-growth.md`

Quarantine here means "not promoted to active set." Move the file out of quarantine. Before moving, check if `audit_hashes.json` (in `Mira/data/soul/`) tracks active skills — if so, register the moved skill there. Look at how other skills (e.g., `verify-before-claiming.md`) appear in the active dir vs the registry, and follow the same convention.

**Important:** the skill file's content (in §1, the part visible from the quarantine directory) describes a content mix and hashtag rules. **Some of those rules conflict with the new persona spine** — e.g., the skill recommends "X Premium gives 10x algorithmic boost. Worth considering." — Mira does NOT pay for X Premium and the engagement-bait orientation of that suggestion violates the friction-as-feature pin. If the skill's content is loaded as prompt context for tweet generation, edit the skill file to:
1. Remove any X Premium recommendations
2. Replace "no generic 'check out my new article' promo" with explicit reference to the agent-vantage gate
3. Add a "what NOT to tweet" section that mirrors the notes-level rules: no abstract "AI is changing X" claims, no listicles, no hashtag soup, no engagement-bait hooks

**Success criterion:** Active skill file exists at `Mira/data/soul/learned/x-twitter-growth.md`, X Premium recommendation removed, agent-vantage gate referenced in the skill's "what to tweet" section.

### T6 — Build the daily tweet cycle with safe ramp
**File:** `/Users/angwei/Sandbox/Mira/agents/socialmedia/twitter.py` + `/Users/angwei/Sandbox/Mira/agents/super/daily_tasks.py`

Look at how `notes.py` schedules itself for 3/day (the existing notes-cycle handler is a good pattern to mirror — find it via `grep -rn "notes.*cycle\|run_notes" agents/`). Implement a parallel `run_tweet_cycle()` that:

1. Reads today's tweet count from `twitter_state.json`
2. Reads today's quota from a new config: `TWITTER_DAILY_QUOTA` in `Mira/lib/config.py` — START AT `5`, NOT 15. Add a comment that this ramps over 2 weeks.
3. If today's count >= quota, return early
4. Otherwise: generate 1 tweet via the writer/skills pipeline (use existing tweet-generation if it exists; otherwise generate a placeholder tweet from the latest journal entry or interesting note)
5. The generated tweet MUST pass `_has_agent_specific_tweet` (already enforced inside the post helpers from T2 — no extra wiring needed, but be aware the cycle may try to generate multiple times to get one that passes)
6. Cap retry at 5 generation attempts per cycle invocation. If 5 attempts fail the gate, log "tweet gate rejected 5/5 generations — skipping cycle" and exit. This is an important signal that the generation step is producing generic-AI-voice content; surface it loudly.

Register the cycle in `daily_tasks.py` `_DAILY_TASK_CONTRACTS` with `window=(9, 22)` (broad enough to spread tweets across the day) and verify via the same pattern as `growth_snapshot`.

**Success criterion:**
- `TWITTER_DAILY_QUOTA = 5` in `Mira/lib/config.py`
- Manual invocation of `run_tweet_cycle()` either posts a tweet or logs why it skipped
- After 24h of natural runs, no more than 5 tweets posted, all passing the gate

### T7 — Wire ramp schedule
**File:** `/Users/angwei/Sandbox/Mira/lib/config.py`

The 5/day quota is for week 1 only. Document the ramp in a comment:

```python
# Twitter daily quota — ramp schedule:
#   Week 1 (post-Essay-1):   5/day  (current)
#   Week 2:                 10/day
#   Week 3+:                15/day
# Manual bump after each week if the previous week's gate-rejection rate
# was <40% AND the follower delta correlation was non-negative.
TWITTER_DAILY_QUOTA = 5
```

The ramp itself is NOT automated — Mira (or her human) bumps it manually after the weekly metrics review. This is intentional friction.

**Success criterion:** Comment is present, quota is 5.

## 5. What NOT to do

- **Do NOT enable Phase 2 if the entry-condition check fails.** Phase 1 alone is a complete useful unit of work.
- **Do NOT bypass the gate for the Essay 1 auto-promo tweet** "because it's important." If the auto-promo can't pass the gate, the auto-promo wording is wrong and needs to be fixed (in `tweet_for_article`), not bypassed.
- **Do NOT set `TWITTER_DAILY_QUOTA` higher than 5** in this task. The ramp is gradual. Aggressive restart violates the friction-as-feature spine.
- **Do NOT add hashtag-spam patterns** to the gate as a way to make more tweets pass. The right move when generation fails the gate is to fix the generator, not weaken the gate.
- **Do NOT touch `twitter_state.json`** to manually adjust history or counters.
- **Do NOT enable X Premium** or recommend it in any skill file.
- **Do NOT use the TodoWrite tool** to break this into 30 micro-tasks. The tasks above are already the right granularity.

## 6. Order and rough effort

| Task | Phase | Depends on | Effort |
|------|-------|------------|--------|
| T1 — `_has_agent_specific_tweet` | 1 | — | 15 min |
| T2 — Hook gate into post paths | 1 | T1 | 20 min |
| T3 — Backwards audit | 1 | T1 | 10 min |
| T4 — Add tweet metrics to snapshot | 1 | — | 20 min |
| T5 — Un-quarantine skill | 2 | Essay 1 + 48h | 15 min |
| T6 — Build cycle with ramp | 2 | T5 | 60 min |
| T7 — Wire ramp config comment | 2 | T6 | 5 min |

Phase 1 is ~65 min and unconditional. Phase 2 is ~80 min and gated.

## 7. When done

Write `/Users/angwei/Sandbox/Mira/HANDOFF_twitter_restart_RESULT.md` with:
- Which phase you completed (Phase 1 only, or Phase 1 + Phase 2)
- For each task: success-criterion evidence (output of the verification command, file diffs, log lines)
- Backwards audit pass rate from T3
- If Phase 2 was skipped: confirmation that the entry-condition check returned `NOT READY` and the reason
- Any unexpected discoveries (e.g., a tweet-emit code path you found that wasn't in the obvious places)
- Any deviations from the spec, with reasoning

Do NOT mark a task complete unless its success criterion was actually verified.
