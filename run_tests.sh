#!/bin/bash
# Run Mira tests
# Usage:
#   ./run_tests.sh          # fast tests only
#   ./run_tests.sh all      # all tests including slow (LLM calls)
#   ./run_tests.sh pipeline # pipeline E2E tests only

cd "$(dirname "$0")"

case "${1:-fast}" in
    fast)
        echo "Running fast tests..."
        python3 -m pytest agents/ -m "not slow" -v
        ;;
    all)
        echo "Running ALL tests (including slow/LLM)..."
        python3 -m pytest agents/ -v
        ;;
    pipeline)
        echo "Running pipeline tests..."
        python3 -m pytest agents/ -m "pipeline" -v
        ;;
    *)
        echo "Usage: $0 {fast|all|pipeline}"
        exit 1
        ;;
esac
