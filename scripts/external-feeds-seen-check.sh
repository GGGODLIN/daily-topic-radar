#!/bin/bash
# external-feeds-seen-check.sh — 把「這條 external-feeds 訊號是不是早就見過」變成機械計算
#
# 動機（2026-07-26）：follow-builders feed 是上游 repo（zarazhangrui/follow-builders）產的，
# 對 publishedAt=null 的舊文每天重複回傳。digest 寫作 agent 看不出「舊文」與「主 pipeline 漏抓」
# 的差別，於是連續三次把同一篇 2026-05-25 的 Anthropic containment 文章記成「backstop 補漏命中」
# （digest 07-09 / 07-18 / 07-26），而該文主 pipeline 早在 2026-06-06 就抓過。
# 上游改不動 → 在消費端把判準算出來，讓寫作 agent 不用猜。
#
# 兩項輸出：
#   1. blogs feed 每個 URL 在「歷史 raw md / 歷史 digest / 歷史 feed 檔」各出現幾次（都排除當天）
#   2. x feed 的 handle 與 sources.yml 內 enabled twitter source 的 handle 交集 / 差集
#
# 契約：一律 exit 0（缺檔也是）、把狀態寫在 stdout marker，讓呼叫端 fail-closed 判讀。
#   BLOGS_SEEN_CHECK=OK|UNAVAILABLE
#   X_HANDLE_OVERLAP=OK|UNAVAILABLE
# 呼叫端規則：非 OK 時不得做「backstop 補漏」或「非主 pipeline 帳號」宣稱。
#
# 用法：bash scripts/external-feeds-seen-check.sh <YYYY-MM-DD>

set -u

DATE="${1:-}"
if [ -z "$DATE" ]; then
  echo "usage: bash scripts/external-feeds-seen-check.sh <YYYY-MM-DD>" >&2
  exit 2
fi

SI="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW_GLOB="$SI/reports/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md"
DIGEST_GLOB="$SI/reports/digest-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].html"
FEED_GLOB="$SI/reports/external-feeds/follow-builders-blogs-*.json"
BLOGS_FILE="$SI/reports/external-feeds/follow-builders-blogs-$DATE.json"
X_FILE="$SI/reports/external-feeds/follow-builders-x-$DATE.json"
SOURCES="$SI/sources.yml"

echo "== blogs seen-check =="
if [ ! -f "$BLOGS_FILE" ]; then
  echo "BLOGS_SEEN_CHECK=UNAVAILABLE reason=blogs feed file not on disk ($BLOGS_FILE)"
elif ! jq -e . "$BLOGS_FILE" >/dev/null 2>&1; then
  echo "BLOGS_SEEN_CHECK=UNAVAILABLE reason=jq parse failed"
else
  urls=$(jq -r '.blogs[]?.url // empty' "$BLOGS_FILE" 2>/dev/null | sort -u)
  if [ -z "$urls" ]; then
    echo "BLOGS_SEEN_CHECK=OK note=blogs feed has 0 items, nothing to check"
  else
    printf '%s\n' "$urls" | while IFS= read -r u; do
      [ -z "$u" ] && continue
      main=$(grep -lF "$u" $RAW_GLOB 2>/dev/null | grep -v "/$DATE.md" | xargs -r -n1 basename | tr '\n' ',' | sed 's/,$//')
      dg=$(grep -lF "$u" $DIGEST_GLOB 2>/dev/null | grep -v "digest-$DATE.html" | wc -l | tr -d ' ')
      fd=$(grep -lF "$u" $FEED_GLOB 2>/dev/null | grep -v -- "-$DATE.json" | wc -l | tr -d ' ')
      pub=$(jq -r --arg u "$u" '.blogs[]? | select(.url == $u) | (.publishedAt // "null")' "$BLOGS_FILE" 2>/dev/null | head -1)
      if [ -z "$main" ] && [ "$dg" = "0" ]; then
        verdict=GENUINE_GAP
      else
        verdict=ALREADY_SEEN
      fi
      echo "URL=$u | verdict=$verdict | main_pipeline_raw_md=${main:-NONE} | prior_digests=$dg | prior_feed_days=$fd | publishedAt=$pub"
    done
    echo "BLOGS_SEEN_CHECK=OK"
  fi
fi

echo "== x handle overlap =="
if [ ! -f "$X_FILE" ]; then
  echo "X_HANDLE_OVERLAP=UNAVAILABLE reason=x feed file not on disk ($X_FILE)"
elif [ ! -f "$SOURCES" ]; then
  echo "X_HANDLE_OVERLAP=UNAVAILABLE reason=sources.yml not found"
else
  python3 - "$SOURCES" "$X_FILE" <<'PY' || echo "X_HANDLE_OVERLAP=UNAVAILABLE reason=python extractor failed"
import json
import sys

sources_path, feed_path = sys.argv[1], sys.argv[2]
try:
    import yaml
except ImportError:
    print('X_HANDLE_OVERLAP=UNAVAILABLE reason=pyyaml not importable')
    raise SystemExit(0)

with open(sources_path) as fh:
    conf = yaml.safe_load(fh)
pipeline = {
    str(h).lower()
    for s in (conf.get('sources') or [])
    if isinstance(s, dict) and s.get('type') == 'twitter' and s.get('enabled')
    for h in (s.get('handles') or [])
}
with open(feed_path) as fh:
    feed = json.load(fh)
feed_handles = {str(a.get('handle', '')).lower() for a in (feed.get('x') or []) if a.get('handle')}
overlap = sorted(feed_handles & pipeline)
fresh = sorted(feed_handles - pipeline)
print('pipeline_enabled_handles=%d' % len(pipeline))
print('feed_handles=%d' % len(feed_handles))
print('overlap_count=%d' % len(overlap))
print('overlap=%s' % ','.join(overlap))
print('new_count=%d' % len(fresh))
print('new=%s' % ','.join(fresh))
print('X_HANDLE_OVERLAP=OK')
PY
fi
