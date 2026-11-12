#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECK="$SCRIPT_DIR/check-digest-template.sh"
TEMPLATE="$REPO_ROOT/templates/digest-template.html"
RAW="$REPO_ROOT/reports/2026-08-18.md"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/check-digest-tpl.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

PASS=0
FAIL=0

expect_pass() {
  local label="$1" html="$2"
  if bash "$CHECK" "$html" "$RAW" >/dev/null 2>&1; then
    echo "PASS: $label"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $label (expected exit 0)" >&2
    FAIL=$((FAIL + 1))
  fi
}

expect_fail() {
  local label="$1" html="$2"
  if bash "$CHECK" "$html" "$RAW" >/dev/null 2>&1; then
    echo "FAIL: $label (expected non-zero exit)" >&2
    FAIL=$((FAIL + 1))
  else
    echo "PASS: $label"
    PASS=$((PASS + 1))
  fi
}

expect_pass "canonical digest-template.html passes all gates" "$TEMPLATE"

sed 's|<head>|<head><link rel="stylesheet" href="https://unlisted-cdn.example.com/font.css">|' "$TEMPLATE" > "$TMP_DIR/bad-url.html"
expect_fail "rejects unlisted external href" "$TMP_DIR/bad-url.html"

sed 's|class="sub"|class="sub-removed"|g' "$TEMPLATE" > "$TMP_DIR/no-sub.html"
expect_fail "rejects missing class=sub sum summary" "$TMP_DIR/no-sub.html"

printf '%s\n' '<tr><td>a</td></tr><tr><td>b</td></tr>' >> "$TMP_DIR/same-line.html"
cat "$TEMPLATE" "$TMP_DIR/same-line.html" > "$TMP_DIR/bad-row.html"
expect_fail "rejects two tr tags on the same line" "$TMP_DIR/bad-row.html"

sed 's|\.wrap {|\.wrap { overflow: hidden;|' "$TEMPLATE" > "$TMP_DIR/clipped-wrap.html"
expect_fail "rejects overflow:hidden on .wrap" "$TMP_DIR/clipped-wrap.html"

grep -vi 'name="viewport"' "$TEMPLATE" > "$TMP_DIR/no-viewport.html"
expect_fail "rejects missing viewport meta tag" "$TMP_DIR/no-viewport.html"

echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
