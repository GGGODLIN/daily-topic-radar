#!/bin/bash
cd /
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/linhancheng/.local/bin:/opt/homebrew/bin"
export PATH

REPO_DIR="/Users/linhancheng/code/social-info"
OUT_DIR="$REPO_DIR/reports/local-analysis"
LOG_DIR="$REPO_DIR/logs"
TARGET="/Users/linhancheng/.claude"
AGNIX_VERSION="0.37.5"
BASELINE="$OUT_DIR/.agnix-baseline"

mkdir -p "$OUT_DIR" "$LOG_DIR"
DATE=$(date +%Y-%m-%d)
OUT="$OUT_DIR/$DATE-agnix.md"
LOG="$LOG_DIR/local-analysis-agnix-$DATE.log"

{
  echo "=== agnix daily started: $(date) ==="

  RAW=$(mktemp)
  CURRENT=$(mktemp)
  trap 'rm -f "$RAW" "$CURRENT"' EXIT

  cd "$TARGET"
  npx -y "agnix@$AGNIX_VERSION" --format json . > "$RAW" 2>/dev/null || true

  python3 - "$RAW" <<'PYEOF' | sort > "$CURRENT"
import json, sys
d = json.load(open(sys.argv[1]))
items = d.get('diagnostics') or d.get('issues') or d.get('results') or []
for i in items:
    f = i['file'].replace('/Users/linhancheng/.claude/', '')
    print(f"{i['level']}|{i['rule']}|{f}|{i['message'][:160].replace('|', '¦')}")
PYEOF

  if [ ! -f "$BASELINE" ]; then
    cp "$CURRENT" "$BASELINE"
    {
      echo "# agnix config-lint baseline 建立 — $DATE"
      echo ""
      echo "首跑，已存 baseline（$(wc -l < "$CURRENT" | tr -d ' ') 條 finding，噪音規則已由 ~/.claude/.agnix.toml 停用）。全量："
      echo ""
      awk -F'|' '{ printf "- [%s] %s `%s` — %s\n", $1, $2, $3, $4 }' "$CURRENT"
    } > "$OUT"
    echo "baseline created"
    exit 0
  fi

  NEW_FINDINGS=$(comm -13 "$BASELINE" "$CURRENT" || true)
  RESOLVED=$(comm -23 "$BASELINE" "$CURRENT" || true)

  if [ -z "$NEW_FINDINGS" ] && [ -z "$RESOLVED" ]; then
    printf '__SILENT__' > "$OUT"
    echo "no delta vs baseline, __SILENT__"
  else
    {
      echo "# agnix config-lint 變化 — $DATE"
      echo ""
      if [ -n "$NEW_FINDINGS" ]; then
        echo "## ⚠️ 新 finding"
        echo ""
        echo "$NEW_FINDINGS" | awk -F'|' '{ printf "- [%s] %s `%s` — %s\n", $1, $2, $3, $4 }'
        echo ""
        echo "處置：error 級先修；warning 級判斷真偽，結構性誤報 → 加進 ~/.claude/.agnix.toml disabled_rules。"
        echo ""
      fi
      if [ -n "$RESOLVED" ]; then
        echo "## ✅ 已解除"
        echo ""
        echo "$RESOLVED" | awk -F'|' '{ printf "- [%s] %s `%s`\n", $1, $2, $3 }'
        echo ""
      fi
    } > "$OUT"
    cp "$CURRENT" "$BASELINE"
    echo "delta reported: new=$(echo "$NEW_FINDINGS" | grep -c . || true) resolved=$(echo "$RESOLVED" | grep -c . || true)"
  fi
} >> "$LOG" 2>&1
