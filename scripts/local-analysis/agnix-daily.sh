#!/bin/bash
cd /
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/linhancheng/.local/bin:/opt/homebrew/bin"
export PATH

REPO_DIR="/Users/linhancheng/code/social-info"
OUT_DIR="$REPO_DIR/reports/local-analysis"
LOG_DIR="$REPO_DIR/logs"
TARGET="${AGNIX_TARGET:-/Users/linhancheng/.claude}"
AGNIX_VERSION="0.37.5"
BASELINE="${AGNIX_BASELINE:-$OUT_DIR/.agnix-baseline}"

mkdir -p "$OUT_DIR" "$LOG_DIR"
DATE="${LOCAL_ANALYSIS_DATE:-$(date +%Y-%m-%d)}"
OUT="${AGNIX_OUT:-$OUT_DIR/$DATE-agnix.md}"
LOG="${AGNIX_LOG:-$LOG_DIR/local-analysis-agnix-$DATE.log}"
mkdir -p "$(dirname "$OUT")" "$(dirname "$LOG")" "$(dirname "$BASELINE")"

{
  echo "=== agnix daily started: $(date) ==="

  RAW=$(mktemp)
  CURRENT=$(mktemp)
  ERROR=$(mktemp)
  trap 'rc=$?; rm -f "$RAW" "$CURRENT" "$ERROR"; exit $rc' EXIT

  AGNIX_RC=0
  if [ -n "${AGNIX_JSON_INPUT:-}" ]; then
    cp "$AGNIX_JSON_INPUT" "$RAW"
  else
    cd "$TARGET"
    npx -y "agnix@$AGNIX_VERSION" --format json . > "$RAW" 2> "$ERROR" || AGNIX_RC=$?
  fi

  if [ ! -s "$RAW" ]; then
    {
      echo "# agnix config-lint 掃描失敗 — $DATE"
      echo ""
      echo "## 🚨 掃描器未產生可用 JSON"
      echo ""
      echo "npx exit code: ${AGNIX_RC}（agnix 有 finding 時本來就以 1 退出，判失敗只看輸出是否為空）。詳見 ${LOG}。baseline 未更新。"
    } > "$OUT"
    [ ! -s "$ERROR" ] || while IFS= read -r line; do printf '%s\n' "$line"; done < "$ERROR"
    echo "agnix execution failed rc=$AGNIX_RC"
    exit 0
  fi

  if ! python3 - "$RAW" <<'PYEOF' | sort > "$CURRENT"
import json, sys
allowed = {
    "Unknown frontmatter field 'upstream'",
    "Unknown frontmatter field 'upstream-path'",
    "Unknown frontmatter field 'upstream-pinned'",
    "Unknown frontmatter field 'upstream-status'",
}
try:
    d = json.load(open(sys.argv[1]))
except (OSError, json.JSONDecodeError) as exc:
    print(f'agnix JSON parse failed: {exc}', file=sys.stderr)
    raise SystemExit(2)
items = d.get('diagnostics') or d.get('issues') or d.get('results') or []
for i in items:
    f = i['file'].replace('/Users/linhancheng/.claude/', '')
    message = i['message'][:160].replace('|', '¦')
    if i['rule'] == 'CC-SK-017' and message in allowed:
        continue
    print(f"{i['level']}|{i['rule']}|{f}|{message}")
PYEOF
  then
    {
      echo "# agnix config-lint 掃描失敗 — $DATE"
      echo ""
      echo "## 🚨 掃描器輸出不是合法 JSON"
      echo ""
      echo "詳見 ${LOG}。baseline 未更新。"
    } > "$OUT"
    echo "agnix JSON parse failed"
    exit 0
  fi

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
