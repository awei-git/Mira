#!/bin/bash
# Daily update for Claude Code and Codex CLI
set -euo pipefail

export PATH="/opt/homebrew/bin:$PATH"

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"

echo "$LOG_PREFIX Starting CLI tools update..."

# Update Claude Code
echo "$LOG_PREFIX Updating Claude Code..."
npm update -g @anthropic-ai/claude-code 2>&1
echo "$LOG_PREFIX Claude Code version: $(claude --version 2>&1)"

# Update Codex CLI
# Note: if codex is installed under /usr/local (root-owned), this installs
# a second copy under homebrew's prefix which takes PATH priority.
echo "$LOG_PREFIX Updating Codex CLI..."
npm update -g @openai/codex 2>&1 || npm install -g @openai/codex 2>&1
echo "$LOG_PREFIX Codex version: $(codex --version 2>&1)"

echo "$LOG_PREFIX Done."
