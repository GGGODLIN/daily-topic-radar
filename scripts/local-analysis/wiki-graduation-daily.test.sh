#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP="$(mktemp -d)"
trap 'rc=$?; rm -rf "$TMP"; exit $rc' EXIT

touch "$TMP/_schema.md" "$TMP/index.md" "$TMP/log.md" "$TMP/alpha.md" "$TMP/beta.md"
actual="$(WIKI_DIR="$TMP" bash "$DIR/wiki-entity-paths.sh")"
expected="$(printf '%s\n' "$TMP/alpha.md" "$TMP/beta.md")"
test "$actual" = "$expected"

prompt="$(bash "$DIR/wiki-graduation-daily.sh")"
grep -F "$DIR/wiki-entity-paths.sh" <<< "$prompt" >/dev/null
grep -F '所有 entity 計數、分桶與候選掃描只能使用這份輸出' <<< "$prompt" >/dev/null

printf 'wiki graduation entity scope: PASS\n'
