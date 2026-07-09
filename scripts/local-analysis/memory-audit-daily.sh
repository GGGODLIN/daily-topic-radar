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
  # consolidated memory：autoMemoryDirectory(~/.claude/memory) 已統一，cwd 無關
  # (2026-05-30；舊 code-projects→Desktop-projects symlink 已移除，/memory-audit 改讀 autoMemoryDirectory)
  cd /
  "$CLAUDE" -p "/memory-audit" > "$OUT" 2>&1
  echo "=== memory audit finished: $(date) ==="
  echo "Output: $OUT ($(wc -c < "$OUT") bytes)"
} >> "$LOG" 2>&1
