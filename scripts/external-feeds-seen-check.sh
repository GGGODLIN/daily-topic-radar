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
#   1. blogs feed 每個 URL 在「raw md（含當天、排除晚於目標日的未來檔）/ 歷史 digest / 歷史 feed 檔」各出現幾次
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

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SI="${SOCIAL_INFO_DIR:-$SCRIPT_ROOT}"
PY_BIN="python3"
if [ -x "$SI/.venv/bin/python" ]; then
  PY_BIN="$SI/.venv/bin/python"
elif [ -x "$SCRIPT_ROOT/.venv/bin/python" ]; then
  PY_BIN="$SCRIPT_ROOT/.venv/bin/python"
fi
BLOGS_FILE="$SI/reports/external-feeds/follow-builders-blogs-$DATE.json"
X_FILE="$SI/reports/external-feeds/follow-builders-x-$DATE.json"
SOURCES="$SI/sources.yml"

echo "== blogs seen-check =="
if [ ! -f "$BLOGS_FILE" ]; then
  echo "BLOGS_SEEN_CHECK=UNAVAILABLE reason=blogs feed file not on disk ($BLOGS_FILE)"
elif ! jq -e . "$BLOGS_FILE" >/dev/null 2>&1; then
  echo "BLOGS_SEEN_CHECK=UNAVAILABLE reason=jq parse failed"
else
  "$PY_BIN" -I - "$SI" "$DATE" "$BLOGS_FILE" <<'PY' || echo "BLOGS_SEEN_CHECK=UNAVAILABLE reason=python blogs check failed"
import glob
import json
import os
import re
import sys

si, target_date, blogs_path = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.load(open(blogs_path, encoding="utf-8"))
blogs = data.get("blogs") or []
urls = sorted({str(b.get("url") or "").strip() for b in blogs if b.get("url")})
if not urls:
    print("BLOGS_SEEN_CHECK=OK note=blogs feed has 0 items, nothing to check")
    raise SystemExit(0)

pub_by_url = {}
for b in blogs:
    u = str(b.get("url") or "").strip()
    if u and u not in pub_by_url:
        pub_by_url[u] = str(b.get("publishedAt") or "null")

date_re = re.compile(r"(\d{4}-\d{2}-\d{2})")
raw_files = []
for p in sorted(glob.glob(os.path.join(si, "reports", "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md"))):
    base = os.path.basename(p)
    m = date_re.search(base)
    if m and m.group(1) <= target_date:
        raw_files.append((base, open(p, encoding="utf-8", errors="replace").read()))

digest_texts = []
for p in sorted(glob.glob(os.path.join(si, "reports", "digest-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].html"))):
    m = date_re.search(os.path.basename(p))
    if m and m.group(1) < target_date:
        digest_texts.append(open(p, encoding="utf-8", errors="replace").read())

feed_texts = []
for p in sorted(glob.glob(os.path.join(si, "reports", "external-feeds", "follow-builders-blogs-*.json"))):
    m = date_re.search(os.path.basename(p))
    if m and m.group(1) < target_date:
        feed_texts.append(open(p, encoding="utf-8", errors="replace").read())

for u in urls:
    hits = [name for name, txt in raw_files if u in txt]
    dg = sum(1 for txt in digest_texts if u in txt)
    fd = sum(1 for txt in feed_texts if u in txt)
    verdict = "GENUINE_GAP" if (not hits and dg == 0) else "ALREADY_SEEN"
    main_str = ",".join(hits) if hits else "NONE"
    print(
        f"URL={u} | verdict={verdict} | main_pipeline_raw_md={main_str} "
        f"| prior_digests={dg} | prior_feed_days={fd} | publishedAt={pub_by_url.get(u, 'null')}"
    )
print("BLOGS_SEEN_CHECK=OK")
PY
fi

echo "== x handle overlap =="
if [ ! -f "$X_FILE" ]; then
  echo "X_HANDLE_OVERLAP=UNAVAILABLE reason=x feed file not on disk ($X_FILE)"
elif [ ! -f "$SOURCES" ]; then
  echo "X_HANDLE_OVERLAP=UNAVAILABLE reason=sources.yml not found"
else
  "$PY_BIN" -I - "$SOURCES" "$X_FILE" <<'PY' || echo "X_HANDLE_OVERLAP=UNAVAILABLE reason=python extractor failed"
import json
import sys

sources_path, feed_path = sys.argv[1], sys.argv[2]
try:
    import yaml
except ImportError:
    print('X_HANDLE_OVERLAP=UNAVAILABLE reason=pyyaml not importable')
    raise SystemExit(0)

with open(sources_path, encoding="utf-8") as fh:
    conf = yaml.safe_load(fh) or {}
pipeline = {
    str(h).lower()
    for s in (conf.get('sources') or [])
    if isinstance(s, dict) and s.get('type') == 'twitter' and s.get('enabled')
    for h in (s.get('handles') or [])
}
try:
    with open(feed_path, encoding="utf-8") as fh:
        feed = json.load(fh) or {}
except ValueError:
    print('X_HANDLE_OVERLAP=UNAVAILABLE reason=x feed json parse failed')
    raise SystemExit(0)
feed_handles = {str(a.get('handle', '')).lower() for a in (feed.get('x') or []) if isinstance(a, dict) and a.get('handle')}
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
