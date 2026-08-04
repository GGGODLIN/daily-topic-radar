#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP="$(mktemp -d)"
trap 'rc=$?; rm -rf "$TMP"; exit $rc' EXIT
BASELINE="$TMP/baseline"
INPUT="$TMP/input.json"
OUT="$TMP/out.md"
LOG="$TMP/run.log"

run_case() {
  AGNIX_BASELINE="$BASELINE" \
  AGNIX_JSON_INPUT="$INPUT" \
  AGNIX_OUT="$OUT" \
  AGNIX_LOG="$LOG" \
  LOCAL_ANALYSIS_DATE="2026-07-29" \
  bash "$DIR/agnix-daily.sh"
}

: > "$BASELINE"
cat > "$INPUT" <<'JSON'
{"diagnostics":[
  {"file":"/Users/linhancheng/.claude/skills/a/SKILL.md","level":"warning","rule":"CC-SK-017","message":"Unknown frontmatter field 'upstream'"},
  {"file":"/Users/linhancheng/.claude/skills/a/SKILL.md","level":"warning","rule":"CC-SK-017","message":"Unknown frontmatter field 'upstream-path'"},
  {"file":"/Users/linhancheng/.claude/skills/a/SKILL.md","level":"warning","rule":"CC-SK-017","message":"Unknown frontmatter field 'upstream-pinned'"},
  {"file":"/Users/linhancheng/.claude/skills/a/SKILL.md","level":"warning","rule":"CC-SK-017","message":"Unknown frontmatter field 'upstream-status'"},
  {"file":"/Users/linhancheng/.claude/skills/a/SKILL.md","level":"warning","rule":"CC-SK-017","message":"Unknown frontmatter field 'upstreamm'"},
  {"file":"/Users/linhancheng/.claude/skills/a/SKILL.md","level":"error","rule":"XML-002","message":"Expected closing tag"}
]}
JSON
run_case
grep -F "Unknown frontmatter field 'upstreamm'" "$OUT" >/dev/null
grep -F 'XML-002' "$OUT" >/dev/null
if grep -F "Unknown frontmatter field 'upstream-path'" "$OUT" >/dev/null; then
  exit 1
fi

BASELINE_HASH="$(shasum -a 256 "$BASELINE" | cut -d' ' -f1)"
printf '{invalid' > "$INPUT"
run_case
grep -F '## 🚨 掃描器輸出不是合法 JSON' "$OUT" >/dev/null
test "$BASELINE_HASH" = "$(shasum -a 256 "$BASELINE" | cut -d' ' -f1)"

printf '2/2 passed\n'
