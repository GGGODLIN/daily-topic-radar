#!/bin/bash
set -euo pipefail

PROJECTS_DIR="${RBA_PROJECTS_DIR:-$HOME/.claude/projects}"
LEDGER="${RBA_LEDGER:-/Users/linhancheng/code/social-info/reports/local-analysis/rba-verify-ledger.jsonl}"
DAYS="${RBA_DAYS:-7}"
if [ -n "${RBA_CUTOFF:-}" ]; then
  CUTOFF="$RBA_CUTOFF"
else
  CUTOFF="$(date -u -v-"${DAYS}"d '+%Y-%m-%dT%H:%M:%S.000Z')"
fi
END="${RBA_END:-$(date -u '+%Y-%m-%dT%H:%M:%S.999Z')}"
TMP="$(mktemp -d)"
trap 'rc=$?; rm -rf "$TMP"; exit $rc' EXIT
REVIEWED="$TMP/reviewed"
CANDIDATES="$TMP/candidates"

if [ -f "$LEDGER" ]; then
  jq -Rr 'fromjson? | select(type == "object") | .session // empty' "$LEDGER" | sed 's/\.jsonl$//' | LC_ALL=C sort -u > "$REVIEWED"
else
  : > "$REVIEWED"
fi

: > "$CANDIDATES"
while IFS= read -r -d '' file; do
  case "$file" in
    */subagents/*|*widget-log*) continue ;;
  esac
  invoke_timestamp=""
  while IFS= read -r timestamp; do
    [[ "$timestamp" < "$CUTOFF" ]] && continue
    [[ "$timestamp" > "$END" ]] && break
    invoke_timestamp="$timestamp"
    break
  done < <(jq -r 'select(.message.content != null) as $row | $row.message.content | if type == "array" then .[] else empty end | select(.type == "tool_use" and .name == "Skill" and (.input.skill // "") == "research-before-answer") | $row.timestamp // empty' "$file" 2>/dev/null)
  [ -n "$invoke_timestamp" ] || continue
  session="$(basename "$file" .jsonl)"
  if ! grep -qxF "$session" "$REVIEWED"; then
    jq -cn --arg session "$session" --arg invoke "$invoke_timestamp" --arg path "$file" '{session: $session, invoke: $invoke, path: $path}' >> "$CANDIDATES"
  fi
done < <(find "$PROJECTS_DIR" -maxdepth 2 -type f -name '*.jsonl' -mtime "-$((DAYS + 1))" -print0 2>/dev/null)

candidates_json="$(jq -sc 'sort_by(.session, .path) | unique_by(.session)' "$CANDIDATES")"
count="$(jq 'length' <<< "$candidates_json")"

if [ "$count" -le 3 ]; then
  samples_json="$candidates_json"
else
  middle=$(((count - 1) / 2))
  last=$((count - 1))
  samples_json="$(jq -c --argjson middle "$middle" --argjson last "$last" '[.[0], .[$middle], .[$last]]' <<< "$candidates_json")"
fi

jq -cn --argjson eligible "$count" --argjson samples "$samples_json" '{eligible: $eligible, samples: $samples}'
