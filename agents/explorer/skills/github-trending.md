# GitHub Trending Analysis

**Tags:** explorer, github, open-source, trends, development

## Core Principle
GitHub Trending shows what engineers are *building and watching right now* — it's the earliest signal of a new technology direction, before blog posts and conference talks arrive. Stars accumulate faster than discourse.

## Dimensions to Query

| Dimension | When useful |
|-----------|-------------|
| No language filter, 7-day window | Broadest view of what's gaining traction |
| Language: Python, 7-day | ML/AI-specific pulse |
| Language: Rust/Go/C, 7-day | Systems/infrastructure pulse |
| Language: TypeScript/JS, 7-day | Frontend/fullstack/dev-tools |
| 1-day window | Breaking momentum (more volatile) |
| 30-day window | Sustained trends (more reliable) |

## Signal vs. Noise Classification

**High signal:**
- Repository created < 6 months ago, 500+ stars/week → genuine inflection
- Infrastructure category (runtimes, compilers, database engines, protocols) → engineers are solving real pain
- "Framework for X" with working examples → adoption indicator
- Repo linked from a recent HN Show HN or arxiv paper → cross-signal confirmation

**Low signal / caution:**
- "Awesome-X" lists → aggregation, not creation
- Tutorial/learning repos → educational, not production signal
- Repos created *on the same day* as a viral tweet → hype without depth
- Stars > 10k but 0 issues filed → untested or showcase-only

## Reading a Repo in 30 Seconds
1. **Name + description**: Is the problem statement clear? Vague names = unclear purpose
2. **README first paragraph**: Does it explain *why*, not just *what*?
3. **Stars vs. forks ratio**: Stars/forks > 10 → people are watching, not using; < 5 → people are building with it
4. **Issues tab**: Are issues substantive technical questions or feature requests? Zero issues + 5k stars → decorative repo
5. **Last commit date**: Active maintenance or abandoned after viral moment?

## Cross-Source Integration
- New repo on GitHub Trending + author post on HN → **strong signal** (two-channel confirmation)
- New ML repo + corresponding arxiv paper → verify paper quality before amplifying
- New language/runtime repo + mentions in r/programming → community adoption signal

## Source
GitHub Search API (`api.github.com/search/repositories?sort=stars&created:>DATE`); trending analysis methodology adapted from OSS monitoring practice.
