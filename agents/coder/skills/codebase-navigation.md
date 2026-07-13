---
activation_trigger: "Apply when you need to understand component connections or locate definitions, callers, and configuration in an unfamiliar codebase."
---

# Codebase Navigation

**Tags:** coding, exploration, understanding

## Trigger
Use this skill when ALL of these are true:
- You need to understand or modify code in a codebase you haven't fully mapped yet
- The task requires understanding how components connect, not just editing a single file
- You need to find where something is defined, called, or configured

Do NOT use when:
- You already know the exact file and function to modify from prior context
- The task is to write new code with no dependencies on existing code

## Start Now
When entering an unfamiliar codebase, follow this exploration sequence:

```
CODEBASE MAP (fill in as you explore):
1. Entry point(s): ___
2. Config/settings files: ___
3. Directory structure pattern: [flat / feature-based / layer-based]
4. Language(s): ___
5. Package manager / dependency file: ___
6. Test location pattern: [alongside / separate tree / both]
7. Key abstractions (top 3 classes/modules): ___
```

### Step 1: Orient (2 minutes)
```bash
# Directory structure — understand the shape
ls -la                          # root: config files, README, entry points
ls */                           # one level deep: major modules
find . -name "*.py" | head -30  # what language, how many files

# Build/config files — understand the toolchain
cat package.json    # or pyproject.toml, Cargo.toml, go.mod, Makefile
cat .env.example    # if it exists — shows what external services are used
```

### Step 2: Find the entry point (2 minutes)
Every codebase has one or more entry points. Find them first:
```bash
# Python
grep -r "if __name__" --include="*.py" -l
grep -r "def main" --include="*.py" -l
cat setup.py | grep "console_scripts"  # or pyproject.toml [project.scripts]

# JavaScript/TypeScript
cat package.json | grep -A2 '"main"'
cat package.json | grep -A5 '"scripts"'

# General
ls -la *.sh bin/ scripts/   # shell entry points
grep -r "app.run\|app.listen\|serve" --include="*.py" --include="*.js" -l
```

### Step 3: Trace from entry point to your target (5 minutes)
From the entry point, follow the call chain toward the code you need to modify:
```bash
# Find where a function/class is defined
grep -rn "def function_name\|class ClassName" --include="*.py"
grep -rn "function functionName\|class ClassName" --include="*.ts" --include="*.js"

# Find where it's called/imported
grep -rn "function_name\|from.*import.*function_name" --include="*.py"
grep -rn "import.*ClassName\|require.*ClassName" --include="*.ts"

# Find related files by naming convention
find . -name "*user*" -o -name "*User*"  # if you're looking for user-related code
```

## Decision Rules

### Read tests first
Tests are the best documentation. They show:
- How the API is intended to be used (setup, call, assertion)
- What edge cases matter
- What the expected behavior is

When you find the module you need to modify, read its tests BEFORE reading the implementation.

### Grep strategies by goal

| Goal | Strategy |
|------|----------|
| Find where X is defined | `grep -rn "def X\|class X\|function X\|const X ="` |
| Find all callers of X | `grep -rn "X(" --include="*.py"` (add `\.X(` for methods) |
| Find config for X | `grep -ri "x" *.json *.yaml *.toml *.env* *.cfg` |
| Find error messages | `grep -rn "error_string_fragment"` — trace from user-visible error to source |
| Understand data flow | `grep -rn "variable_name"` — follow it from creation to consumption |
| Find all implementations of interface | `grep -rn "implements Interface\|extends Base\|class.*Base"` |

### File naming conventions to recognize
- `*_test.py`, `*.test.ts`, `*_spec.rb` — test files
- `__init__.py` — Python package marker; read it to see the public API
- `index.ts`, `index.js` — module entry point; shows what's exported
- `types.ts`, `models.py`, `schema.py` — data definitions; read these early
- `utils.py`, `helpers.ts` — shared utilities; often dependency-heavy
- `config.py`, `settings.py` — configuration; reveals external dependencies

### When you're lost
If you can't find what you need after 3 targeted searches:
1. Search for a distinctive string from the user-visible output (error message, UI label, API response field)
2. Search for the database table/collection name if it's a data issue
3. Look at recent git commits: `git log --oneline -20 --all -- "*.py"` to see what files people actually change
4. Read the README or docs/ directory — often has architecture diagrams or module descriptions

## Failure Modes

### 1. Depth-first rabbit hole
**What happens:** You follow one import chain 10 levels deep and lose track of where you started and why.
**How to catch:** Set a timer. If you've been tracing for more than 5 minutes without finding your target, stop, re-read the original goal, and try a different search strategy.

### 2. Assuming from file names
**What happens:** You assume `user_service.py` handles user creation because of the name. It actually only handles user queries; creation is in `registration.py`.
**How to catch:** Always verify by reading the file or grepping for the specific function, not by guessing from names.

### 3. Missing the second definition
**What happens:** You find `def process()` in `module_a.py` and assume that's the one being called. But `module_b.py` also defines `def process()` and that's the one actually imported.
**How to catch:** When you find a definition, also check the import statement at the call site to confirm which module it comes from.

### 4. Outdated mental model
**What happens:** You mapped the codebase at the start of the session. Since then, you or another process modified files. Your mental map is stale.
**How to catch:** Re-read any file you haven't looked at in the last 10+ minutes before making assumptions about its contents.
