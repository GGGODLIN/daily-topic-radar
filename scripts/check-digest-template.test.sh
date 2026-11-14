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

python3 -I - "$TEMPLATE" "$TMP_DIR/reconciled-ok.html" "$TMP_DIR/reconciled-same-line.html" <<'PY'
import sys
tpl, ok_out, bad_out = sys.argv[1], sys.argv[2], sys.argv[3]
src = open(tpl, encoding="utf-8").read()
anchor = "      </table>\n    </div>"
assert anchor in src, "missing repo table closing anchor in template"

valid_block = (
  anchor
  + "\n    <details class=\"repo-reconciled\">\n"
  + "      <summary>已評估重現（2 筆：immich-app/immich — ⏭ 既評不採用、milanvarady/Applite — ⏭ 既評不採用）</summary>\n"
  + "      <ul>\n"
  + "        <li><a href=\"https://github.com/immich-app/immich\">immich-app/immich</a> — ⏭ 既評不採用；自架相片管理非目標範疇</li>\n"
  + "        <li><a href=\"https://github.com/milanvarady/Applite\">milanvarady/Applite</a> — ⏭ 既評不採用；維持純 CLI 操作 Homebrew</li>\n"
  + "      </ul>\n"
  + "    </details>"
)
open(ok_out, "w", encoding="utf-8").write(src.replace(anchor, valid_block, 1))

invalid_block = (
  anchor
  + "\n    <details class=\"repo-reconciled\">\n"
  + "      <summary>已評估重現（2 筆：immich-app/immich、milanvarady/Applite）</summary>\n"
  + "      <ul>\n"
  + "        <li><a href=\"https://github.com/immich-app/immich\">immich-app/immich</a></li><li><a href=\"https://github.com/milanvarady/Applite\">milanvarady/Applite</a></li>\n"
  + "      </ul>\n"
  + "    </details>"
)
open(bad_out, "w", encoding="utf-8").write(src.replace(anchor, invalid_block, 1))
PY
expect_pass "accepts details.repo-reconciled with single-line li items" "$TMP_DIR/reconciled-ok.html"
expect_fail "rejects details.repo-reconciled with two li tags on the same line" "$TMP_DIR/reconciled-same-line.html"

echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
