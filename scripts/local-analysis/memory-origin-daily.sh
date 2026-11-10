#!/bin/bash
# memory-origin-daily.sh — 每日「記憶筆記回原對話核對」增量 channel（shell）。
# 只查上次起點之後改過的筆記，對照最後修改它的那段對話；判斷交給 SWE-2 Max 與 Terra max（聯集）。
# 全量還沒跑完（沒有起點檔）就靜默跳過，避免把 1,281 條全塞進每日 10 分鐘的 channel。
set -euo pipefail
cd /
PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/Users/linhancheng/.local/bin"
export PATH
REPO_DIR="/Users/linhancheng/code/social-info"
OUT_DIR="$REPO_DIR/reports/local-analysis"
DATE="${LOCAL_ANALYSIS_DATE:-$(date +%Y-%m-%d)}"
OUT="$OUT_DIR/$DATE-memory-origin.md"
STATE="$OUT_DIR/memory-origin"
CURSOR="$STATE/cursor.json"
RUN="$STATE/runs/daily-$DATE"
SCRIPT="$REPO_DIR/scripts/local-analysis/memory-origin.py"

if [ ! -s "$CURSOR" ]; then
  printf '__SILENT__\n' > "$OUT"
  exit 0
fi

# 每輪最多 30 條、每個模型同時 5 條：一次呼叫約 1 分鐘，壓在 channel 的 10 分鐘上限內；沒做完的留給明天。
# GPT 週額度不設暫停：使用者 2026-11-10 說週額度用完可以扣 credit，要跑到完。
python3 -I "$SCRIPT" scan --scope changed --cursor "$CURSOR" --cap 30 --workers 5 --gpt-guard 0 --run-dir "$RUN" \
  --model swe2max=group/swe2max-pool --model terra=group/legacy-codex-gpt-6-1-terra-max
python3 -I "$SCRIPT" report --run-dir "$RUN" --date "$DATE" --out "$OUT" --silent-if-empty
python3 -I "$SCRIPT" commit-cursor --run-dir "$RUN" --cursor "$CURSOR"
