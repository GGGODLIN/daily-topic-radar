#!/bin/bash
cd /
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/linhancheng/.local/bin"
export PATH

CLAUDE="/Users/linhancheng/.local/bin/claude"
REPO_DIR="/Users/linhancheng/code/social-info"
OUT_DIR="$REPO_DIR/reports/local-analysis"
LOG_DIR="$REPO_DIR/logs"

mkdir -p "$OUT_DIR" "$LOG_DIR"
DATE=$(date +%Y-%m-%d)
OUT="$OUT_DIR/$DATE-codemap.md"
LOG="$LOG_DIR/local-analysis-codemap-$DATE.log"

cd "$REPO_DIR"

PROMPT=$(cat <<'EOF'
跑 codemap drift 檢查（runtime 掃 project enumeration，不釘死 list）：

1. 掃 /Users/linhancheng/Desktop/projects/* 和 /Users/linhancheng/Desktop/work/* 找含 docs/CODEMAPS/ 目錄的 project
2. 對每個有 codemap 的 project（用 absolute path，因 launchd 環境可能影響 ~/Desktop 存取）：
   - 看 codemap 最後 git commit 時間（git log -1 --format=%cI docs/CODEMAPS/）跟 mtime
   - 看 src code 主要目錄（src/, lib/, app/, components/, pages/, routes/, server/）自 codemap 最後 update 後的 commit 數（git log <since> --oneline）
   - 看主要 .ts/.tsx/.js/.py/.go/.rs 檔案變動數
3. 估 drift 程度：commit 數 + 主要檔案變動 + 主要 src dir mtime 跟 codemap mtime 差距

輸出格式：
## 掃描範圍
- 掃了 N 個 dir，找到 M 個含 docs/CODEMAPS/
- 列出找到的 project 路徑

## Drift 明顯（建議 update）
- <project path>: 自上次 codemap update <N> 天 / <X> commits / 主要動到 <files>；建議 invoke /update-codemaps

## 輕微 drift（觀察）
## 無 drift / 最近已 update
## 無 codemap 的 project（snapshot only，不算 candidate）

嚴格 read-only：絕對不執行 /update-codemaps，不寫任何 codemap file。只產 markdown 到 stdout。

注意：如果掃 /Users/linhancheng/Desktop/ 路徑遇到 permission denied（macOS launchd TCC 限制），明確報告「Desktop TCC 擋」並繼續掃其他位置；不要 silent fail。
EOF
)

{
  echo "=== codemap drift started: $(date) ==="
  "$CLAUDE" -p "$PROMPT" > "$OUT" 2>&1
  echo "=== codemap drift finished: $(date) ==="
  echo "Output: $OUT ($(wc -c < "$OUT") bytes)"
} >> "$LOG" 2>&1
