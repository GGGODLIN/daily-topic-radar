#!/bin/bash
cd /
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/linhancheng/.local/bin"
export PATH
# headless channel run: 走 hook Defense 0 跳過 nudge 類 Stop hook（checkpoint-judge 曾把最後一則訊息蓋成「skip」、claude -p stdout 只印最後一則，2026-07-15 查因）
export CC_VENDOR=headless-channel

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

⚠️ `/update-codemaps` command 已於 2026-06-11（commit c1abd32）退役。**不要建議「invoke /update-codemaps」**。

建議動作改推按 [[feedback_codemap_marginal_value]] 規範的具體流程（5/2 user 拍板）：
- 真值 = **entry-file pinpoint**（每個 module / extension 標 entry 路徑）+ **alias finding 副產物**（順手抓 alias.md 增量、結構異常）+ **cross-file trace**（page → hook → api → store 路徑）
- ❌ 不再做 mind map（純人類視覺、token 預算珍貴）
- ❌ 不要 ls-style exhaustive listing（變「ls 的 markdown 版」）
- 派 subagent 跑 rescan 時 prompt 內 explicit 要求「順手抓 alias / 結構異常」副產物

**具體執行 command**：`/refresh-codemaps`（akocommerce 專屬、`~/.claude/commands/refresh-codemaps.md`、按上述規範派 5 個 subagent 並行 refresh + 寫 commit msg 模板）。對非 akocommerce 的 project（如 cc-i18n-proxy）目前無對應 command、user 自行決定要不要手動 rescan 或 fork 一份 command。

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

## Drift 明顯（建議 rescan）
- <project path>: 自上次 codemap update <N> 天 / <X> commits / 主要動到 <files>；建議派 subagent 做 entry-file rescan + alias finding + cross-file trace（具體目標：補新增的 entry 路徑、抓本次積壓的結構/alias 異常、為新模組畫 cross-file trace）。**akocommerce 跑 `/refresh-codemaps` command 一次到位**；其他 project 目前無對應 command、手動派 subagent 或 fork。

## 輕微 drift（觀察）
## 無 drift / 最近已 update
## 無 codemap 的 project（snapshot only，不算 candidate）

嚴格 read-only：不執行任何 codemap update / rewrite，不寫任何 codemap file。只產 markdown drift report 到 stdout。

注意：如果掃 /Users/linhancheng/Desktop/ 路徑遇到 permission denied（macOS launchd TCC 限制），明確報告「Desktop TCC 擋」並繼續掃其他位置；不要 silent fail。
EOF
)

{
  echo "=== codemap drift started: $(date) ==="
  "$CLAUDE" -p "$PROMPT" > "$OUT" 2>&1
  echo "=== codemap drift finished: $(date) ==="
  echo "Output: $OUT ($(wc -c < "$OUT") bytes)"
} >> "$LOG" 2>&1
