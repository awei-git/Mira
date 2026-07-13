#!/bin/bash
# Nightly commit & push this repository.
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"

if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
    echo "SKIP $repo (clean)"
    exit 0
fi

git add -A
git commit -m "nightly auto-commit $(date +%Y-%m-%d)" || true
git push || echo "WARN: push failed for $repo"
echo "OK $repo"
