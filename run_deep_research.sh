#!/usr/bin/env bash
#
# StockDrop: Deep Research Backfill
# ==================================
# Processes BUY-rated stocks through Deep Research that were missed
# when the main tool stopped.
#
# Usage:
#   ./run_deep_research.sh                     # today (fallback yesterday)
#   ./run_deep_research.sh --date 2026-02-07   # specific date
#   ./run_deep_research.sh --dry-run           # preview only
#   ./run_deep_research.sh --limit 3           # first 3 candidates
#

set -euo pipefail

# Resolve project root (where this script lives)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Run the Python backfill script, forwarding all arguments
exec python3 scripts/run_deep_research_backfill.py "$@"
