#!/bin/bash
# Nightly commit & push all repos
set -euo pipefail

REPOS=(
    "/Users/angwei/Sandbox/Mira"
    "/Users/angwei/Sandbox/Tetra"
    "/Users/angwei/Sandbox/MasterMinds"
)

for repo in "${REPOS[@]}"; do
    if [ ! -d "$repo/.git" ]; then
        echo "SKIP $repo (not a git repo)"
        continue
    fi
    cd "$repo"
    
    # Skip if nothing to commit
    if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
        echo "SKIP $repo (clean)"
        continue
    fi
    
    git add -A
    git commit -m "nightly auto-commit $(date +%Y-%m-%d)" || true
    git push || echo "WARN: push failed for $repo"
    echo "OK $repo"
done
