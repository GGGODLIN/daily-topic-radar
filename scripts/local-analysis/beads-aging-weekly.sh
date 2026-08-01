#!/bin/bash
cd /
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/linhancheng/.local/bin:/opt/homebrew/bin"
export PATH

REPO_DIR="/Users/linhancheng/code/social-info"
OUT_DIR="$REPO_DIR/reports/local-analysis"
LOG_DIR="$REPO_DIR/logs"

mkdir -p "$OUT_DIR" "$LOG_DIR"
DATE="${LOCAL_ANALYSIS_DATE:-$(date +%Y-%m-%d)}"
OUT="${BEADS_AGING_OUT:-$OUT_DIR/$DATE-beads-aging.md}"
LOG="${BEADS_AGING_LOG:-$LOG_DIR/local-analysis-beads-aging-$DATE.log}"

{
  printf '=== beads-aging started: %s ===\n' "$(date)"
  python3 "$REPO_DIR/scripts/local-analysis/beads-aging.py" --date "$DATE" --out "$OUT"
} >> "$LOG" 2>&1
