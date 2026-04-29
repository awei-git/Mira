---
activation_trigger: "Apply when considering adding, updating, auditing, or removing a third-party package or library from a codebase."
---

# Dependency Management

**Tags:** coding, dependencies, security

## Trigger
Use this skill when ALL of these are true:
- You need functionality not currently in the codebase
- You are considering adding a third-party package or library
- OR you need to update, audit, or remove an existing dependency

Do NOT use when:
- The functionality already exists in the standard library
- You are writing internal utility code with no external packages involved

## Start Now
Before adding ANY dependency, complete this evaluation:

```
DEPENDENCY EVALUATION:
Package: ___
Purpose: ___

1. [ ] Can I implement this myself in < 50 lines? If YES → write it yourself
2. [ ] Is this available in the standard library? If YES → use stdlib
3. [ ] Last commit date: ___ (reject if > 12 months for active domain)
4. [ ] Weekly downloads / GitHub stars: ___ (proxy for community support)
5. [ ] Open issue count & response time: ___ (maintained or abandoned?)
6. [ ] License: ___ (MIT/Apache/BSD = OK. GPL = check compatibility. SSPL/BSL = caution)
7. [ ] Transitive dependencies: ___ (how many sub-deps does it pull in?)
8. [ ] Known vulnerabilities: ___ (check `npm audit`, `pip-audit`, `cargo audit`, or Snyk)
9. [ ] Does it have tests? ___ (no tests = no confidence)
```

## Decision Rules

### Build vs. Buy decision tree
```
Need external functionality?
├── Available in standard library? → Use stdlib. Stop.
├── Can implement in < 50 lines? → Write it yourself. Stop.
├── Is it a well-solved problem with tricky edge cases?
│   (crypto, date/time, HTTP, CSV parsing, image processing)
│   → Use a well-established library. Stop.
├── Is it a thin wrapper around an API?
│   → Write it yourself with `requests`/`fetch`. Stop.
└── Is it complex domain logic (ML, PDF generation, video encoding)?
    → Use a library. Evaluate carefully.
```

### Why < 50 lines matters
Small dependencies create hidden costs:
- Supply chain attack surface (every dep is a trust boundary)
- Version conflicts with other deps
- Upgrade churn when they release breaking changes
- Abandonment risk (maintainer stops updating)

A 20-line utility function you own is safer, faster to debug, and never breaks unexpectedly.

### Version pinning rules
- **Always pin exact versions in production**: `requests==2.31.0` not `requests>=2.31`
- **Lock files are mandatory**: `package-lock.json`, `poetry.lock`, `Cargo.lock` — commit them
- **Never use `*` or `latest`** as a version specifier
- **Pin transitive deps** via lock files — direct pins alone are not enough

### How to pin (by ecosystem)
```bash
# Python (poetry)
poetry add requests@2.31.0    # pins in pyproject.toml + poetry.lock

# Python (pip)
pip install requests==2.31.0
pip freeze > requirements.txt  # pin everything

# Node.js
npm install express@4.18.2 --save-exact
# package-lock.json handles transitive pins

# Rust
# Cargo.lock handles this automatically — commit it
```

### Safe upgrade workflow
1. **Read the changelog** for the new version — look for breaking changes
2. **Upgrade one dependency at a time** — never batch upgrades
3. **Run the full test suite** after each upgrade
4. **Check for deprecation warnings** in test output
5. **Commit the upgrade separately** from any code changes

```bash
# Check what's outdated
pip list --outdated          # Python
npm outdated                  # Node.js
cargo outdated                # Rust

# Upgrade one at a time
pip install package==new_version
npm install package@new_version
cargo update -p package
```

### When to remove a dependency
- You use < 20% of its features and could replace what you use with 30 lines
- It hasn't been updated in 18+ months and has open security issues
- It pulls in 50+ transitive dependencies for one function you use
- Its license changed to something incompatible

## Failure Modes

### 1. Left-pad syndrome
**What happens:** You add a dependency for a trivial function (padding a string, checking if a number is odd). The package gets unpublished or compromised. Your build breaks or gets hijacked.
**How to catch:** Before adding any package, check its size. If the source is under 50 lines, copy the logic instead.

### 2. Phantom dependency
**What happens:** Your code imports a package that works because it's a transitive dependency of something else. You never explicitly installed it. When the parent dependency updates and drops it, your code breaks.
**How to catch:** Every import in your code must correspond to an explicit entry in your dependency file. Grep your imports and cross-reference with `requirements.txt` / `package.json`.

### 3. Unpinned drift
**What happens:** You specify `requests>=2.0` and it works in development. In production, `pip install` grabs version 3.0 which has breaking changes. Your app crashes.
**How to catch:** Always use a lock file. Test with `pip install -r requirements.txt` (not `pip install -e .`) to simulate production.

### 4. License contamination
**What happens:** You add a GPL-licensed library to a proprietary project. Now your entire project may need to be GPL-licensed.
**How to catch:** Check licenses before adding. MIT, Apache 2.0, and BSD are permissive (safe for any project). GPL, AGPL, and SSPL have copyleft obligations. Use `pip-licenses` or `license-checker` to audit.

### 5. Security vulnerabilities in transitive deps
**What happens:** Your direct dependency is fine, but it depends on a package with a known CVE. You never see it because you only check your direct deps.
**How to catch:** Run `npm audit`, `pip-audit`, `cargo audit`, or use Dependabot/Snyk. Audit transitive deps, not just direct ones. Do this on a schedule, not just at install time.
