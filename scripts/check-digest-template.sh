#!/usr/bin/env bash
# check-digest-template.sh — 每日話題 HTML 版型範本與結構契約檢查
# 用法：bash scripts/check-digest-template.sh [html_path] [raw_md_path]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HTML_PATH="${1:-$REPO_ROOT/templates/digest-template.html}"
if [ -n "${2:-}" ]; then
  RAW_MD_PATH="$2"
else
  html_base="$(basename "$HTML_PATH")"
  if [[ "$html_base" =~ ^digest-([0-9]{4}-[0-9]{2}-[0-9]{2})\.html$ ]]; then
    inferred_date="${BASH_REMATCH[1]}"
    same_dir_raw="$(dirname "$HTML_PATH")/${inferred_date}.md"
    if [ -f "$same_dir_raw" ]; then
      RAW_MD_PATH="$same_dir_raw"
    else
      RAW_MD_PATH="$REPO_ROOT/reports/${inferred_date}.md"
    fi
  else
    RAW_MD_PATH="$REPO_ROOT/reports/2026-08-18.md"
  fi
fi
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

if ! grep -qi '<meta[^>]*name="viewport"' "$HTML_PATH"; then
  echo "❌ Contract 1b FAIL: missing <meta name=\"viewport\"> in $HTML_PATH (breaks @media mobile layout)" >&2
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

import html.parser

class HygieneParser(html.parser.HTMLParser):
    SKIP = frozenset({"style", "script", "code", "pre", "textarea"})
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.buf = []
        self.buf_ln = 1
    def flush(self):
        if not self.buf:
            return
        seg = "".join(self.buf)
        base = self.buf_ln
        self.buf = []
        for off, line in enumerate(seg.split("\n")):
            ln = base + off
            if re.search(r"`[^`\n]{1,120}`", line):
                print(f"❌ Contract 6 FAIL: stray Markdown backticks in HTML prose at L{ln} (use <code>...</code>)", file=sys.stderr)
                sys.exit(1)
            if re.search(r"\*\*(?=\S)[^*\n]{1,120}?(?<=\S)\*\*", line):
                print(f"❌ Contract 6 FAIL: stray Markdown **bold** in HTML prose at L{ln} (use <strong>...</strong>)", file=sys.stderr)
                sys.exit(1)
    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        ln = self.getpos()[0]
        if t in self.SKIP:
            self.flush()
            self.skip += 1
        elif t in {"p", "li", "td", "th", "tr", "div", "section", "h1", "h2", "h3", "h4", "summary", "br"}:
            self.flush()
        for k, v in attrs:
            if k.lower() != "href":
                continue
            val = v or ""
            if val == "" or val != val.strip() or ((t == "a" or not val.lower().startswith("data:")) and re.search(r"\s", val)):
                print(f"❌ Contract 5 FAIL: invalid empty or whitespace-containing href on <{t}> at L{ln}", file=sys.stderr)
                sys.exit(1)
    def handle_endtag(self, tag):
        t = tag.lower()
        if t in self.SKIP:
            if self.skip > 0:
                self.skip -= 1
        elif t in {"p", "li", "td", "th", "tr", "div", "section", "h1", "h2", "h3", "h4", "summary"}:
            self.flush()
    def handle_data(self, data):
        if self.skip > 0:
            return
        if not self.buf:
            self.buf_ln = self.getpos()[0]
        self.buf.append(data)
    def close(self):
        self.flush()
        super().close()

hp = HygieneParser()
hp.feed(txt)
hp.close()
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
