#!/bin/bash
set -euo pipefail

WORKFLOW="/Users/linhancheng/.claude/workflows/local-analysis.js"

grep -F '用 Write 工具把完整 markdown report 寫到' "$WORKFLOW" >/dev/null
grep -F '若檔案已存在，先用 Read 工具讀取後再覆寫' "$WORKFLOW" >/dev/null
grep -F '不要用 Bash redirect 寫報告' "$WORKFLOW" >/dev/null
grep -F '其他寫回也優先用專用 mutation 工具' "$WORKFLOW" >/dev/null
grep -F '只有 touch 這類無專用工具的操作才用 Bash' "$WORKFLOW" >/dev/null
grep -F '不得與動態 redirect 合併成同一條指令' "$WORKFLOW" >/dev/null
if grep -F '用 Bash 把完整 markdown report 寫到' "$WORKFLOW" >/dev/null; then
  exit 1
fi

printf 'local-analysis workflow report contract: PASS\n'
