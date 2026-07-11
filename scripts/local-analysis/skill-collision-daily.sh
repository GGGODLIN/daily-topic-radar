#!/bin/bash
cd /
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/linhancheng/.local/bin:/opt/homebrew/bin"
export PATH

REPO_DIR="/Users/linhancheng/code/social-info"
OUT_DIR="$REPO_DIR/reports/local-analysis"
LOG_DIR="$REPO_DIR/logs"
CHECKER="/Users/linhancheng/.claude/scripts/skill-collision-check.js"
BASELINE="$OUT_DIR/.skill-collision-baseline"

mkdir -p "$OUT_DIR" "$LOG_DIR"
DATE=$(date +%Y-%m-%d)
OUT="$OUT_DIR/$DATE-skill-collision.md"
LOG="$LOG_DIR/local-analysis-skill-collision-$DATE.log"

{
  echo "=== skill-collision daily started: $(date) ==="

  CURRENT=$(mktemp)
  trap 'rm -f "$CURRENT"' EXIT
  node "$CHECKER" --pairs-only | sort > "$CURRENT"

  if [ ! -f "$BASELINE" ]; then
    cp "$CURRENT" "$BASELINE"
    {
      echo "# Skill collision baseline 建立 — $DATE"
      echo ""
      echo "首跑，已存 baseline（$(wc -l < "$CURRENT" | tr -d ' ') pair）。全量清單："
      echo ""
      node "$CHECKER"
    } > "$OUT"
    echo "baseline created"
    exit 0
  fi

  NEW_PAIRS=$(comm -13 "$BASELINE" "$CURRENT" || true)
  RESOLVED=$(comm -23 "$BASELINE" "$CURRENT" || true)

  if [ -z "$NEW_PAIRS" ] && [ -z "$RESOLVED" ]; then
    printf '__SILENT__' > "$OUT"
    echo "no delta vs baseline, __SILENT__"
  else
    {
      echo "# Skill description 碰撞變化 — $DATE"
      echo ""
      if [ -n "$NEW_PAIRS" ]; then
        echo "## ⚠️ 新出現的相似 pair（skill 路由擇一衝突候選）"
        echo ""
        echo "$NEW_PAIRS" | awk -F'|' '{ printf "- %s ↔ %s（%.0f%%）\n", $1, $2, $3*100 }'
        echo ""
        echo "處置：檢查兩者 description 的觸發面是否真的互搶；是 → 補反向排除或收窄措辭。"
        echo ""
      fi
      if [ -n "$RESOLVED" ]; then
        echo "## ✅ 已解除的 pair"
        echo ""
        echo "$RESOLVED" | awk -F'|' '{ printf "- %s ↔ %s\n", $1, $2 }'
        echo ""
      fi
    } > "$OUT"
    cp "$CURRENT" "$BASELINE"
    echo "delta reported: new=$(echo "$NEW_PAIRS" | grep -c . || true) resolved=$(echo "$RESOLVED" | grep -c . || true)"
  fi
} >> "$LOG" 2>&1
