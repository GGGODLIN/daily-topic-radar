#!/usr/bin/env bash
# check-digest-template.sh — 每日話題 HTML 版型範本與結構契約檢查
# 用法：bash scripts/check-digest-template.sh [html_path] [raw_md_path]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HTML_PATH="${1:-$REPO_ROOT/templates/digest-template.html}"
RAW_MD_PATH="${2:-$REPO_ROOT/reports/2026-08-18.md}"
VERIFY_SH="${VERIFY_DIGEST_SH:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/verify-daily-digest.sh}"

if [ ! -f "$HTML_PATH" ]; then
  echo "❌ HTML file not found: $HTML_PATH" >&2
  exit 2
fi
if [ ! -f "$RAW_MD_PATH" ]; then
  echo "❌ Raw markdown file not found: $RAW_MD_PATH" >&2
  exit 2
fi

if ! grep -qi '<!doctype html>' "$HTML_PATH" || ! grep -q '<style>' "$HTML_PATH" || ! grep -q '</style>' "$HTML_PATH"; then
  echo "❌ Contract 1 FAIL: missing <!DOCTYPE html> or <style>...</style> block in $HTML_PATH" >&2
  exit 1
fi

if ! python3 -I - "$HTML_PATH" <<'PY'
import re, sys
txt = open(sys.argv[1], encoding="utf-8", errors="replace").read()
subs = re.findall(r'class="sub">([^<]*(?:<code>[^<]*</code>[^<]*)*)', txt)
ok = any(("｜" in s and re.search(r"共\s*[\d,]+\s*items", s)) for s in subs)
if not ok:
    print('❌ Contract 2 FAIL: missing class="sub" summary with "｜...共 N items" (required so H3 sum check does not silently skip)', file=sys.stderr)
    sys.exit(1)

style_blocks = re.findall(r"<style>(.*?)</style>", txt, re.S | re.I)
for blk in style_blocks:
    for rule in re.findall(r"([^{}]+)\{([^{}]+)\}", blk):
        sel, body = rule[0].strip(), rule[1]
        if re.search(r"(^|[\s,])(\.wrap|table)(\s|,|$|:)", sel) and re.search(r"overflow\s*:\s*hidden\b", body, re.I):
            print(f"❌ Contract 4 FAIL: selector '{sel}' sets overflow: hidden, which clips wide tables", file=sys.stderr)
            sys.exit(1)
PY
then
  exit 1
fi

if command grep -nE '</tr>.*<tr>|</li>.*<li>' "$HTML_PATH" >&2; then
  echo "❌ Contract 3 FAIL: multiple <tr> or <li> elements on the same line in $HTML_PATH" >&2
  exit 1
fi

if [ -f "$VERIFY_SH" ]; then
  bash "$VERIFY_SH" "$HTML_PATH" "$RAW_MD_PATH"
elif [ "${ALLOW_MISSING_EXTERNAL_VERIFY:-0}" = "1" ]; then
  echo "⚠️ External verify script absent ($VERIFY_SH), standalone structural checks passed"
else
  echo "❌ External verify script not found: $VERIFY_SH" >&2
  exit 2
fi

echo "✅ check-digest-template OK: $HTML_PATH"
