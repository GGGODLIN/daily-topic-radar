#!/bin/bash
cd /
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/linhancheng/.local/bin"
export PATH

CLAUDE="/Users/linhancheng/.local/bin/claude"
REPO_DIR="/Users/linhancheng/code/social-info"
OUT_DIR="$REPO_DIR/reports/local-analysis"
LOG_DIR="$REPO_DIR/logs"

mkdir -p "$OUT_DIR" "$LOG_DIR"
DATE=$(date +%Y-%m-%d)
OUT="$OUT_DIR/$DATE-memory.md"
LOG="$LOG_DIR/local-analysis-memory-$DATE.log"

{
  echo "=== memory audit started: $(date) ==="
  # cd 到 ~/code/projects 透 ~/.claude/projects/ symlink 對應主 memory dir
  # (-Users-linhancheng-code-projects → -Users-linhancheng-Desktop-projects)
  # 避開 ~/Desktop TCC；同 social-info code↔Desktop symlink pattern
  cd /Users/linhancheng/code/projects
  "$CLAUDE" -p "/memory-audit" > "$OUT" 2>&1
  echo "=== memory audit finished: $(date) ==="
  echo "Output: $OUT ($(wc -c < "$OUT") bytes)"
} >> "$LOG" 2>&1
