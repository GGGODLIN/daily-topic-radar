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

cat > "$BASELINE" <<'BASELINE'
warning|AS-013|vendor/sepia/skills/sepia/SKILL.md|File reference 'references/domains/dev-replies.md`' is deeper than one level
warning|AS-013|vendor/sepia/skills/sepia/SKILL.md|File reference 'references/domains/postmortems.md`' is deeper than one level
warning|AS-013|vendor/sepia/skills/sepia/SKILL.md|File reference 'references/domains/release-notes.md`' is deeper than one level
warning|AS-013|vendor/sepia/skills/sepia/SKILL.md|File reference 'references/domains/tech-articles.md`' is deeper than one level
warning|AS-013|vendor/sepia/skills/sepia/SKILL.md|File reference 'references/domains/tickets.md`' is deeper than one level
BASELINE
cat > "$INPUT" <<'JSON'
{"diagnostics":[
  {"file":"/Users/linhancheng/.claude/skills/a/SKILL.md","level":"warning","rule":"CC-SK-017","message":"Unknown frontmatter field 'upstream'"},
  {"file":"/Users/linhancheng/.claude/skills/a/SKILL.md","level":"warning","rule":"CC-SK-017","message":"Unknown frontmatter field 'upstream-path'"},
  {"file":"/Users/linhancheng/.claude/skills/a/SKILL.md","level":"warning","rule":"CC-SK-017","message":"Unknown frontmatter field 'upstream-pinned'"},
  {"file":"/Users/linhancheng/.claude/skills/a/SKILL.md","level":"warning","rule":"CC-SK-017","message":"Unknown frontmatter field 'upstream-status'"},
  {"file":"/Users/linhancheng/.claude/skills/a/SKILL.md","level":"warning","rule":"CC-SK-017","message":"Unknown frontmatter field 'upstreamm'"},
  {"file":"/Users/linhancheng/.claude/vendor/sepia/skills/sepia/SKILL.md","level":"warning","rule":"AS-013","message":"File reference 'references/domains/dev-replies.md`' is deeper than one level"},
  {"file":"/Users/linhancheng/.claude/vendor/sepia/skills/sepia/SKILL.md","level":"warning","rule":"AS-013","message":"File reference 'references/domains/postmortems.md`' is deeper than one level"},
  {"file":"/Users/linhancheng/.claude/vendor/sepia/skills/sepia/SKILL.md","level":"warning","rule":"AS-013","message":"File reference 'references/domains/release-notes.md`' is deeper than one level"},
  {"file":"/Users/linhancheng/.claude/vendor/sepia/skills/sepia/SKILL.md","level":"warning","rule":"AS-013","message":"File reference 'references/domains/tech-articles.md`' is deeper than one level"},
  {"file":"/Users/linhancheng/.claude/vendor/sepia/skills/sepia/SKILL.md","level":"warning","rule":"AS-013","message":"File reference 'references/domains/tickets.md`' is deeper than one level"},
  {"file":"/Users/linhancheng/.claude/skills/workflow-hardening/SKILL.md","level":"warning","rule":"AS-013","message":"File reference 'references/archive/example.md`' is deeper than one level"},
  {"file":"/Users/linhancheng/.claude/skills/a/SKILL.md","level":"error","rule":"XML-002","message":"Expected closing tag"}
]}
JSON
run_case
grep -F "Unknown frontmatter field 'upstreamm'" "$OUT" >/dev/null
grep -F 'XML-002' "$OUT" >/dev/null
if grep -F "Unknown frontmatter field 'upstream-path'" "$OUT" >/dev/null; then
  exit 1
fi
grep -F 'skills/workflow-hardening/SKILL.md' "$OUT" >/dev/null
if grep -F 'vendor/sepia/skills/sepia/SKILL.md' "$OUT" >/dev/null; then
  exit 1
fi
if grep -F '## ✅ 已解除' "$OUT" >/dev/null; then
  exit 1
fi
if grep -F 'vendor/sepia/skills/sepia/SKILL.md' "$BASELINE" >/dev/null; then
  exit 1
fi

BASELINE_HASH="$(shasum -a 256 "$BASELINE" | cut -d' ' -f1)"
printf '{invalid' > "$INPUT"
run_case
grep -F '## 🚨 掃描器輸出不是合法 JSON' "$OUT" >/dev/null
test "$BASELINE_HASH" = "$(shasum -a 256 "$BASELINE" | cut -d' ' -f1)"

printf '2/2 passed\n'
