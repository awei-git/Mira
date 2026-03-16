# Reddit Hot Posts — Signal Extraction

**Tags:** explorer, reddit, social-signals, trends, community

## Core Principle
Reddit is the fastest-moving public discourse layer — news breaks here before traditional media, and subreddit quality ranges from expert community to noise machine. The signal is in subreddit selection and comment-to-upvote pattern reading, not in the posts themselves.

## High-Signal Subreddits by Domain

**AI/ML:**
- `r/MachineLearning` — researchers, paper discussions, high standard
- `r/LocalLLaMA` — practitioners running local models, real-world feedback
- `r/artificial` — broader AI discussion, more varied quality
- `r/learnmachinelearning` — questions reveal what practitioners are struggling with

**Technology:**
- `r/programming` — engineering culture, best practices debates
- `r/compsci` — theoretical CS, algorithm discussions
- `r/sysadmin` — infrastructure reality (what actually breaks in production)
- `r/netsec` — security community, disclosure threads

**Science:**
- `r/science` — AMA threads with researchers are high value; general posts mixed
- `r/physics` — good for theoretical discussions
- `r/math` — proof discussions, accessible exposition

**Current events / geopolitics:**
- `r/worldnews` — breaking news, high volume, use for temporal awareness only
- `r/geopolitics` — slower, more analytical than worldnews

## Quality Scoring

**High-confidence post (deep-read):**
- Upvote:comment ratio 3:1 to 10:1 — engagement without noise
- Original Research / AMA flair
- Cross-posted from primary source (arxiv, official blog, government data)

**Noise patterns (skip):**
- `r/MachineLearning` posts that are screenshots of ChatGPT conversations
- Posts with 5k upvotes and 20 comments — viral but not substantive
- Repost within 30 days (Reddit cycling)
- Link to a blog post summarizing another blog post

## Comment Section Reading
For posts worth understanding deeply:
1. Sort by **Top** — community consensus
2. Read top comment: is it correction, amplification, or context?
3. Look for **distinguished comments** (mod/OP) — adds verified info
4. "Controversial" sorted comments — understand counterarguments

## Temporal Signal Value
- **Within 2 hours of posting**: raw community reaction — volatile but fresh
- **6–24 hours**: settled consensus beginning to form
- **3+ days**: post is a reference, reaction is complete — useful for retrospective

## Subreddit-Specific Norms
- `r/MachineLearning`: Self-posts require [D] (Discussion), [P] (Project), [R] (Research) tags — respect them for filtering
- `r/LocalLLaMA`: Performance benchmarks in posts are often anecdotal — verify before citing
- `r/programming`: Political framing of technical posts → lower technical content

## Integration with Feed Pipeline
- Reddit hot from `r/MachineLearning` + same paper on arxiv → **double signal** for briefing priority
- Reddit sentiment on a product launch → cross-check against HN discussion for balance
- `r/LocalLLaMA` model release reaction → proxy for whether open-source community finds it useful

## Source
Reddit JSON API (`old.reddit.com/r/{sub}/hot.json`); subreddit quality assessment from multi-year monitoring; community signal extraction from journalism/intelligence literature.
