# Hacker News Signal Extraction

**Tags:** explorer, hackernews, curation, tech, signal-noise

## Core Principle
HN's front page is a collective filter run by engineers and founders — its value is not the linked articles but the *comment sections*, which often contain corrections, experience reports, and dissenting evidence the original article omits.

## Feed Types and When to Use Each

| Feed | API tag | Best for |
|------|---------|----------|
| Front page | `front_page` | Broad tech/science/startup pulse |
| New | `story` | Fast signal — unvalidated, high noise |
| Ask HN | `ask_hn` | Real practitioner questions + community answers |
| Show HN | `show_hn` | New tools and projects seeking feedback |
| Jobs | `job` | Hiring trends, stack preferences by company |

**Default fetch:** `front_page`, `min_points >= 50` — filters out early noise.

## Quality Signals

**High-confidence signal (worth deep read):**
- Score > 200 with < 50 comments → passive appreciation of a hard truth
- Score > 100 with > 200 comments → controversy worth understanding
- "Ask HN" with > 300 points → community has collective experience

**Noise patterns (skip or skim):**
- Score/comment ratio is extreme: 500 points, 5 comments → link bait
- Duplicate links cycling monthly (HN re-ranks old posts when shared again)
- Domain: medium.com, substack.com with generic framing ("why X is dead")

## Comment Quality Filter
Before reading the article, read the top 3 comments. If they:
- Correct factual errors in the title → the article is unreliable
- Link to better source → follow that instead
- Ask "has anyone actually done this?" with no answers → claim is unverified

## Temporal Patterns
- **7–10am US Eastern**: stories posted overnight rise to front page — highest density of good material
- **Friday afternoon**: off-topic, nostalgia, and "Ask HN: career advice" spikes — lower signal

## Integration with Information Triage
Apply Information Triage scores:
- HN story with score + engaged comments = **signal score +1**
- HN story citing new research = route to arxiv cross-check
- Show HN with working demo = **route to GitHub Trending cross-check**

## Source
Hacker News Algolia API (`hn.algolia.com/api/v1`); empirical patterns from feed monitoring.
