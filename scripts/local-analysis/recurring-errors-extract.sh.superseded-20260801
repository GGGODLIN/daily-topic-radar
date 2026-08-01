#!/bin/bash
# recurring-errors-extract.sh — recurring-errors channel 的確定性前段（2026-07-11 建，awesome-claude-code absorb #18-1）。
# 增量掃 ~/.claude/projects jsonl 的 is_error tool_result → 正規化簽名 → append ledger。
# LLM 永遠只讀聚合後的簽名表，不掃 raw session（成本控制的核心設計）。
# 首跑回看 180 天（背景跑一次較久），之後只掃 state file 之後的新檔。
set -uo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/linhancheng/.local/bin:/opt/homebrew/bin"
export PATH

STATE="$HOME/.claude/state/recurring-errors.last_run"
LEDGER="$HOME/code/social-info/reports/local-analysis/recurring-errors-ledger.jsonl"
mkdir -p "$HOME/.claude/state" "$(dirname "$LEDGER")"
touch "$LEDGER"

NOW_MARKER=$(mktemp)

if [ -f "$STATE" ]; then
  FILE_FILTER=(-newer "$STATE")
else
  FILE_FILTER=(-mtime -180)
fi

scanned=0
appended=0

while IFS= read -r f; do
  [ -z "$f" ] && continue
  scanned=$((scanned + 1))
  session=$(basename "$f" .jsonl)
  # 抽 is_error tool_result 文字 → 正規化簽名：
  # 去路徑 / 去數字 / 去 hex id / 壓空白 / 轉小寫 / 截 140 字
  jq -r '
    select(.type == "user") | .message.content? // empty |
    if type == "array" then .[] else empty end |
    select(.type? == "tool_result" and .is_error == true) |
    (.content | if type == "array" then (map(select(.type? == "text") | .text) | join(" ")) else tostring end)
  ' "$f" 2>/dev/null | while IFS= read -r err; do
    [ -z "$err" ] && continue
    sig=$(printf '%s' "$err" \
      | tr '[:upper:]' '[:lower:]' \
      | sed -E 's#/[^ ]+##g; s/[0-9a-f]{8,}//g; s/[0-9]+//g; s/[[:space:]]+/ /g' \
      | cut -c1-140 | sed 's/^ *//; s/ *$//')
    [ -z "$sig" ] && continue
    [ "${#sig}" -lt 15 ] && continue
    # 同 session 同簽名去重
    if ! grep -qF "\"sig\":$(jq -Rc . <<<"$sig"),\"session\":\"$session\"" "$LEDGER" 2>/dev/null; then
      jq -nc --arg d "$(date +%Y-%m-%d)" --arg s "$sig" --arg ss "$session" \
        '{date:$d, sig:$s, session:$ss}' >> "$LEDGER"
    fi
  done
done < <(find "$HOME/.claude/projects" -name '*.jsonl' -not -path '*/subagents/*' "${FILE_FILTER[@]}" 2>/dev/null)

appended=$(wc -l < "$LEDGER" | tr -d ' ')
mv "$NOW_MARKER" "$STATE"
echo "scanned=$scanned ledger_total=$appended"
