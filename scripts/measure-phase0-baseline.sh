#!/usr/bin/env bash
# Phase 0 baseline: count hi + med tier GitHub repo entries per day
# in past 14 days of digest-*.html (before scope expansion + resurface).
set -euo pipefail

REPO="/Users/linhancheng/code/social-info"
cd "$REPO"

TOTAL_HI=0
TOTAL_MED=0
DAYS=0
for i in $(seq 0 13); do
  d=$(date -v-${i}d +%F 2>/dev/null || date -d "$i days ago" +%F)
  f="reports/digest-${d}.html"
  [ -f "$f" ] || continue
  # verdict class 命中 = hi/med row（見 CLAUDE.md 「🛠 GitHub 倉庫觀察」段 verdict classes）
  hi=$(grep -c 'class="verdict"' "$f" 2>/dev/null || true)
  med=$(grep -c 'class="verdict-watch"' "$f" 2>/dev/null || true)
  echo "${d}: hi=${hi} med=${med}"
  TOTAL_HI=$((TOTAL_HI + hi))
  TOTAL_MED=$((TOTAL_MED + med))
  DAYS=$((DAYS + 1))
done

if [ "$DAYS" -eq 0 ]; then
  echo "no digest files in past 14 days"
  exit 1
fi

AVG_HI=$(awk "BEGIN {printf \"%.1f\", $TOTAL_HI / $DAYS}")
AVG_MED=$(awk "BEGIN {printf \"%.1f\", $TOTAL_MED / $DAYS}")
AVG_TOTAL=$(awk "BEGIN {printf \"%.1f\", ($TOTAL_HI + $TOTAL_MED) / $DAYS}")

echo ""
echo "=== Phase 0 Baseline ($DAYS days) ==="
echo "avg hi / day:  $AVG_HI"
echo "avg med / day: $AVG_MED"
echo "avg hi+med / day: $AVG_TOTAL"
