#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="$DIR/rba-verify-sample.sh"
WRAPPER="$DIR/rba-verify-weekly.sh"
WORKFLOW="/Users/linhancheng/.claude/workflows/local-analysis.js"
TMP="$(mktemp -d)"
trap 'rc=$?; rm -rf "$TMP"; exit $rc' EXIT
PROJECTS="$TMP/projects"
LEDGER="$TMP/ledger.jsonl"

add_session() {
  local project="$1"
  local session="$2"
  local skill="${3:-research-before-answer}"
  local timestamp="${4:-2026-07-28T12:00:00.000Z}"
  mkdir -p "$PROJECTS/$project"
  printf '%s\n' "{\"timestamp\":\"$timestamp\",\"message\":{\"content\":[{\"type\":\"tool_use\",\"name\":\"Skill\",\"input\":{\"skill\":\"$skill\"}}]}}" > "$PROJECTS/$project/$session.jsonl"
}

add_session zeta 10000000-0000-0000-0000-000000000000
add_session alpha 20000000-0000-0000-0000-000000000000
add_session theta 30000000-0000-0000-0000-000000000000
add_session beta 40000000-0000-0000-0000-000000000000
add_session gamma 50000000-0000-0000-0000-000000000000
add_session delta 60000000-0000-0000-0000-000000000000
add_session epsilon 70000000-0000-0000-0000-000000000000
add_session aardvark 80000000-0000-0000-0000-000000000000
add_session duplicate 30000000-0000-0000-0000-000000000000
add_session $'tab\tproject' 99000000-0000-0000-0000-000000000000
add_session zero 05000000-0000-0000-0000-000000000000 another-skill
add_session omega 90000000-0000-0000-0000-000000000000 research-before-answer 2026-07-20T12:00:00.000Z
mkdir -p "$PROJECTS/iota"
printf '%s\n' \
  '{"timestamp":"2026-07-20T12:00:00.000Z","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"research-before-answer"}}]}}' \
  '{"timestamp":"2026-07-28T12:00:00.000Z","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"research-before-answer"}}]}}' > "$PROJECTS/iota/95000000-0000-0000-0000-000000000000.jsonl"
mkdir -p "$PROJECTS/zeta/subagents"
printf '%s\n' '{"message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"research-before-answer"}}]}}' > "$PROJECTS/zeta/subagents/01000000-0000-0000-0000-000000000000.jsonl"

printf '%s\n' \
  '{"session":"20000000-0000-0000-0000-000000000000"}' \
  '{invalid' \
  '{"session":"70000000-0000-0000-0000-000000000000.jsonl"}' > "$LEDGER"

sample_json="$(RBA_PROJECTS_DIR="$PROJECTS" RBA_LEDGER="$LEDGER" RBA_CUTOFF="2026-07-22T00:00:00.000Z" "$HELPER")"
test "$(jq -r '.eligible' <<< "$sample_json")" = "8"
test "$(jq -c '[.samples[].session]' <<< "$sample_json")" = '["10000000-0000-0000-0000-000000000000","50000000-0000-0000-0000-000000000000","99000000-0000-0000-0000-000000000000"]'
test "$(jq -r '.samples[0].path' <<< "$sample_json")" = "$PROJECTS/zeta/10000000-0000-0000-0000-000000000000.jsonl"
test "$(jq -r '.samples[2].path' <<< "$sample_json")" = "$PROJECTS/"$'tab\tproject'"/99000000-0000-0000-0000-000000000000.jsonl"
test "$(jq -r '.samples[2].invoke' <<< "$sample_json")" = "2026-07-28T12:00:00.000Z"

prompt="$(bash "$WRAPPER")"
grep -F 'rba-verify-sample.sh' <<< "$prompt" >/dev/null
grep -F '每個 session 只評 `.samples[].invoke` timestamp 指定的 research-before-answer invoke' <<< "$prompt" >/dev/null
grep -F 'subagents/workflows/<runId>/agent-*.jsonl' <<< "$prompt" >/dev/null
grep -F '回答產生後的工具結果不得倒灌' <<< "$prompt" >/dev/null
grep -F '來源互相衝突' <<< "$prompt" >/dev/null
grep -F '先列出受評答案中所有會改變使用者決策或直接回答使用者問題的具體主張' <<< "$prompt" >/dev/null
grep -F '不得只抽查最容易通過的 1 至 2 點' <<< "$prompt" >/dev/null
grep -F '跨來源合併帳號／方案／額度池等身分時' <<< "$prompt" >/dev/null
grep -F '「無 FAIL」或「0 FAIL」等敘述不算前週失敗' <<< "$prompt" >/dev/null
grep -F '/Users/linhancheng/code/social-info/reports/local-analysis/<今日減 7 日>-rba-verify.md' <<< "$prompt" >/dev/null
grep -F '檔案不存在就不算連續週' <<< "$prompt" >/dev/null

grep -F "{ key: 'rba-verify', freq: 'weekly-tue', kind: 'llm', src: \`\${W}/rba-verify-weekly.sh\`, model: 'opus', effort: 'medium' }," "$WORKFLOW" >/dev/null
grep -F 'const channelAgentOptions = (c) =>' "$WORKFLOW" >/dev/null
grep -F '...channelAgentOptions(c),' "$WORKFLOW" >/dev/null
grep -F "c.channel === 'rba-verify'" "$WORKFLOW" >/dev/null
grep -F 'Read ${c.report_path} and preserve any ⚠️ or 🚨 line verbatim.' "$WORKFLOW" >/dev/null
node --check "$WORKFLOW"

printf '22/22 passed\n'
