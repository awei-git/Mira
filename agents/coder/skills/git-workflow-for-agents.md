# Git Workflow for Agents

**Tags:** coding, git, workflow, safety

## Trigger
Use this skill when ALL of these are true:
- You are making changes to a git-tracked codebase
- The changes will persist beyond this session (not throwaway experiments)
- You need to commit, branch, or manage version history

Do NOT use when:
- Working in a directory that is not a git repository
- Making temporary changes you intend to discard

## Start Now
Before every git operation, run this status check:

```
PRE-OPERATION CHECK:
1. [ ] `git status` — know what's modified, staged, and untracked
2. [ ] `git diff` — review exactly what changed (no surprises in the commit)
3. [ ] `git log --oneline -5` — know the recent history and message style
4. [ ] No secrets in the diff (API keys, tokens, passwords, .env files)
5. [ ] No large binary files accidentally staged
```

### Commit template
```
<type>: <what changed and why> (< 72 chars)

<optional body: context, motivation, trade-offs>

Co-Authored-By: <agent identity if applicable>
```

Types: `fix`, `feat`, `refactor`, `test`, `docs`, `chore`, `perf`

## Decision Rules

### When to commit
- **Commit after each logical unit of work** — one feature, one bug fix, one refactor
- **Never combine unrelated changes** in a single commit
- **Commit working code** — if tests exist, they should pass at every commit
- **Commit before risky operations** — about to do a big refactor? Commit the current state first

### What NOT to commit
- Files with secrets (`.env`, credentials, API keys) — check every diff
- Generated files that can be rebuilt (`node_modules/`, `__pycache__/`, `.pyc`, `dist/`)
- Large binary files (images, videos, models) unless the repo is specifically for them
- Temporary debugging code (`print("HERE")`, `console.log("DEBUG")`)

### Branch strategy
- **Feature work:** Create a branch from main: `git checkout -b feat/description`
- **Bug fixes:** Branch from main: `git checkout -b fix/description`
- **Never commit directly to main** for non-trivial changes
- **Keep branches short-lived** — merge within days, not weeks

### Staging discipline
- **Stage specific files by name:** `git add path/to/file.py` — never use `git add -A` or `git add .` without reviewing first
- **Review after staging:** `git diff --staged` to confirm exactly what will be committed
- If you accidentally stage something, `git reset HEAD <file>` to unstage (safe, non-destructive)

### Operations that are NEVER safe for an agent
- `git push --force` — destroys remote history; never do this
- `git reset --hard` — destroys uncommitted work; avoid unless explicitly instructed
- `git clean -f` — permanently deletes untracked files
- `git rebase` on shared branches — rewrites history others depend on
- `git checkout .` or `git restore .` — discards all uncommitted changes

### Commit message rules
- First line: imperative mood, present tense ("add", "fix", "remove" — not "added", "fixed")
- First line under 72 characters
- Body explains WHY, not WHAT (the diff shows what)
- Reference issue numbers if applicable
- Match the existing repo's style — if they use `[component]` prefixes, you should too

## Failure Modes

### 1. Committing secrets
**What happens:** An API key or password ends up in git history. Even if you remove it in the next commit, it's in the history forever.
**How to catch:** Before every commit: `git diff --staged | grep -i -E "(api.?key|secret|token|password|credential)"`. Check `.gitignore` covers `.env` files.

### 2. Mega-commits
**What happens:** You make 15 changes across 8 files in one commit. When something breaks, you can't bisect to find the cause and can't revert without losing good changes.
**How to catch:** If your `git diff --stat` shows more than 5 files or 200 lines changed, consider splitting into multiple commits.

### 3. Committing broken code
**What happens:** You commit mid-change. The commit doesn't compile or tests fail. This breaks `git bisect` and makes the history unreliable.
**How to catch:** Run tests before committing. At minimum, check that the changed files parse without syntax errors.

### 4. Wrong branch
**What happens:** You commit a feature directly to main, or commit a fix to a feature branch that should go to main.
**How to catch:** Always check `git branch` before committing. If you committed to the wrong branch, use `git cherry-pick` to move it (don't force push).

### 5. Merge conflicts from stale branches
**What happens:** Your branch diverged from main weeks ago. The merge is painful and error-prone.
**How to catch:** Regularly check `git log main..HEAD --oneline` to see how far you've diverged. Merge main into your branch frequently, or keep branches short-lived.

### 6. Forgetting to check status after operations
**What happens:** You think the commit succeeded, but it didn't (pre-commit hook failed, merge had conflicts). You proceed as if the state is clean.
**How to catch:** Always run `git status` after every commit or merge to verify the result matches your expectation.
