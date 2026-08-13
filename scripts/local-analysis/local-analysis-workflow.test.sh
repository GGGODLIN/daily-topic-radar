#!/bin/bash
set -euo pipefail

WORKFLOW="/Users/linhancheng/.claude/workflows/local-analysis.js"

grep -F '用 Write 工具把完整 markdown report 寫到' "$WORKFLOW" >/dev/null
grep -F '若檔案已存在，先用 Read 工具讀取後再覆寫' "$WORKFLOW" >/dev/null
if grep -F '用 Bash 把完整 markdown report 寫到' "$WORKFLOW" >/dev/null; then
  exit 1
fi

printf 'local-analysis workflow report contract: PASS\n'
