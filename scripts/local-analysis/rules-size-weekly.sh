#!/bin/bash
cd /
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/linhancheng/.local/bin"
export PATH

REPO_DIR="/Users/linhancheng/code/social-info"
OUT_DIR="$REPO_DIR/reports/local-analysis"
LOG_DIR="$REPO_DIR/logs"

mkdir -p "$OUT_DIR" "$LOG_DIR"
DATE="${LOCAL_ANALYSIS_DATE:-$(date +%Y-%m-%d)}"
OUT="$OUT_DIR/$DATE-rules-size.md"
LOG="$LOG_DIR/local-analysis-rules-size-$DATE.log"

# P-len 門檻（harness-audit-2026-07-03 拍板；env 可覆寫供測試）
# 校準依據補充（2026-07-11 自 HumanLayer "Writing a Good CLAUDE.md" 收）：
# LLM 可穩定遵循 ~150-200 條 instruction（CC system prompt 已占 ~50 條）、
# 單檔理想 <300 行、prompt 周邊位置（開頭/結尾）注意力較高。
# byte cap 是 proxy——若未來要調 cap，用「指令條數」重新換算而非直接放大 bytes。
CLAUDE_MD="/Users/linhancheng/.claude/CLAUDE.md"
CLAUDE_CAP="${RULES_SIZE_CLAUDE_CAP:-12288}"   # 12KB
RULE_CAP="${RULES_SIZE_RULE_CAP:-9728}"        # 9.5KB
TOTAL_CAP="${RULES_SIZE_TOTAL_CAP:-46080}"     # 45KB

{
  echo "=== rules-size weekly started: $(date) ==="

  VIOLATIONS=$(mktemp)
  trap 'rc=$?; rm -f "$VIOLATIONS"; exit $rc' EXIT
  TOTAL=0

  check_file() {
    local f="$1" cap="$2"
    [ -f "$f" ] || return 0
    local size
    size=$(wc -c < "$f" | tr -d ' ')
    TOTAL=$((TOTAL + size))
    if [ "$size" -gt "$cap" ]; then
      echo "| \`$f\` | $size | $cap | 超 $((size - cap)) bytes |" >> "$VIOLATIONS"
    fi
  }

  check_file "$CLAUDE_MD" "$CLAUDE_CAP"
  for f in /Users/linhancheng/.claude/rules/common/*.md /Users/linhancheng/.claude/rules/external/*.md; do
    check_file "$f" "$RULE_CAP"
  done

  TOTAL_LINE=""
  if [ "$TOTAL" -gt "$TOTAL_CAP" ]; then
    TOTAL_LINE="⚠️ scope 總和 ${TOTAL} bytes 超過 cap ${TOTAL_CAP}（45KB）"
  fi

  HIT_COUNT=$(wc -l < "$VIOLATIONS" | tr -d ' ')

  if [ "$HIT_COUNT" -eq 0 ] && [ -z "$TOTAL_LINE" ]; then
    printf '__SILENT__' > "$OUT"
    echo "all within caps (total=$TOTAL bytes), __SILENT__"
  else
    {
      echo "# Rules 檔長度健康度 Weekly — $DATE"
      echo ""
      echo "**門檻（P-len、harness-audit-2026-07-03 拍板）**：CLAUDE.md ≤ ${CLAUDE_CAP} bytes、rules 檔 ≤ ${RULE_CAP} bytes、scope 總和 ≤ ${TOTAL_CAP} bytes"
      echo "**Scope**：~/.claude/CLAUDE.md + rules/common/*.md + rules/external/*.md（不含 MEMORY.md、project CLAUDE.md）"
      echo ""
      if [ "$HIT_COUNT" -gt 0 ]; then
        echo "## 超標檔案（${HIT_COUNT}）"
        echo ""
        echo "| 檔案 | 現況 bytes | Cap | 超標量 |"
        echo "|---|---|---|---|"
        cat "$VIOLATIONS"
        echo ""
      fi
      if [ -n "$TOTAL_LINE" ]; then
        echo "$TOTAL_LINE"
        echo ""
      fi
      echo "## 動作"
      echo ""
      echo "超標檔案在下次想加新段前先瘦身：刪過時段落、或下放 memory cluster（詳細規則與歷史脈絡見 ~/Desktop/projects/harness-audit-2026-07-03.md P-len 段）。門檻的目的是強迫「先刪再加」、不是禁止成長——確有必要成長時調 cap 並在本 wrapper 檔頭留一行原因。"
    } > "$OUT"
    echo "violations=$HIT_COUNT total=$TOTAL → report written"
  fi

  echo "=== rules-size weekly finished: $(date) ==="
  echo "Output: $OUT ($(wc -c < "$OUT") bytes)"
} >> "$LOG" 2>&1
