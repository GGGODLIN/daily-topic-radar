#!/bin/bash
# deep-research-pending-daily.sh — PROMPT container for /daily-local workflow's deep-research-pending channel.
#
# Read by ~/.claude/workflows/local-analysis.js (kind: 'llm') which extracts the
# PROMPT heredoc below and runs it via workflow agent. Running `bash` on this file
# runs claude -p standalone (for ad-hoc inspection / testing).
#
# Purpose: surface deep-research raw reports that don't have a corresponding wiki entity,
# closing the gap where wiki-candidates only scans ~/.claude/memory/ + ~/.claude/wiki/
# and leaves memory-backlog-research/runs/<date>/deep-research/ invisible to the daily audit.
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
OUT="$OUT_DIR/$DATE-deep-research-pending.md"
LOG="$LOG_DIR/local-analysis-deep-research-pending-$DATE.log"

cd "$REPO_DIR"

PROMPT=$(cat <<'EOF'
你是 deep-research pending audit agent。任務：掃 `~/Desktop/projects/memory-backlog-research/runs/<date>/deep-research/` 全部日期 dir 的 `.md` raw 研究報告，對照 `~/.claude/wiki/` 找「raw 存在但 wiki 缺對應 entity」的 pending promote 機會。

Channel 角色：補 wiki-candidates 只掃 `~/.claude/memory/` + `~/.claude/wiki/` 的 gap——deep-research workflow 直接落檔的 raw 報告對 wiki audit 是隱形的，靠本 channel 補。

# 第一原則：永遠輸出完整 6 段 markdown

不論候選數量、不論判準是否全失敗，**第一個 byte 就是 `## 掃描範圍`**。6 段標題逐字照抄、缺一不可：

```
## 掃描範圍
（時間 / 掃了幾個 run dir / 共 N 個 raw 報告 / wiki entity 數 / slug exact-match + drift 統計）

## 前一日候選 follow-up
（昨日候選 + 對 `~/.claude/wiki/<slug>.md` 的查驗結果；無候選寫「前一日無候選」；昨日無報告寫「無前一日報告」）

## Pending promote 候選
（raw 存在 + wiki 缺對應 + mature 訊號 ≥7d 的研究；用 `### 候選 N: <slug>` 格式列；**無候選寫「無新候選」、不省略段標題**）

### 候選 N: <slug>
- raw 路徑: `runs/<date>/deep-research/<slug>.md`
- 研究日期: distinct 距今 N 天
- TL;DR 摘要（從 raw .md head 抽）
- wiki gap 分析: 跟 `~/.claude/wiki/` 既有 entity 是否相關、是否補位

## 不確定 / 待你決定
（slug drift carry-forward：wiki 有 short name 但 raw 是 `-2026` / `-batchN` / 日期後綴變體；**無候選寫「無」、不省略段標題**）

## 已有對應 wiki entity (snapshot only)
（raw 已 promote 的對照表 + wiki entity 最近 mtime；幫忙判斷需不需要 refresh）

## 已掃但不夠 mature
（raw 報告 mtime < 7 天前的 carry-forward；幫忙下次跑時 follow-up）
```

**禁止輸出**（理由：歷史報告 baseline 應 ~3-8KB，短輸出 = short-circuit failure 不是真的無候選）：
- 單行 `skip` / `無候選` / 空檔 / 任何 < 500 bytes 報告
- 整份報告用 code fence 包起來
- Preamble（「以下是 ...」「整理完...」「以下提供...」）
- 略過任何 1 個段標題

今日 < 500 bytes 視為 short-circuit failure。

# Promote-status 標記（最優先，先於下面判準）

掃到的 raw `.md` 若 frontmatter 出現 `wiki_promote:` 欄位（少數研究報告會手動 annotate），按其值處理：

- `declined` / `declined-*`：使用者已拍板不 promote → **完全跳過**，不列進任何段
- `hold-*`（如 `hold-until-trial`）：列進「不確定 / 待你決定」段、標註「⏸ HOLD: <值>」+ 一行 hold 理由，但**不催促拍板、不累計久懸天數**

# 判準（用於 §Pending promote 候選 filtering）

raw 報告同時滿足 4 條才列進「Pending promote 候選」段：

1. raw `.md` 存在於 `~/Desktop/projects/memory-backlog-research/runs/<date>/deep-research/`
2. 報告 mtime ≥ 7 天前（內容已穩定不在 batch 中）
3. 內容 entity-centric（topic 是工具 / landscape / pattern / model，**排除 internal artifact**：`BATCH-STATE` / `workflow-batch*` / `self-verify-log*` / `pool-inventory` / `recommendations-by-type` / `agent-*` 過程性檔）
4. 對照 `~/.claude/wiki/index.md` + ls `~/.claude/wiki/*.md`：該主題沒對應 wiki entity（含 slug drift 比對：去除 `-YYYY` 後綴 / `-batchN` 後綴 / 日期前綴）

不滿足全 4 條的 raw 報告**不漏掉**、改放：

- 滿足 1+2+3 但 4 不確定（slug drift：raw 是 `apple-silicon-local-llm-serving-2026.md`、wiki 是 `apple-silicon-local-llm-serving.md`）→「不確定 / 待你決定」段、標註 drift 對應疑似 entity 給 user 拍板
- 滿足 1+2+3+4 反轉（已有覆蓋 = refresh 候選）→「已有對應 wiki entity」段、列 wiki entity mtime vs raw mtime 看是否該 refresh
- 1/2 失敗（raw 不夠 mature）→「已掃但不夠 mature」段

「已掃但不夠 mature」段是 carry-forward 機制：raw mtime 4-6 天時列出來、寫「預估 N 天後 ≥ 7d cap 可重評」，下次跑時 follow-up 段可直接抓。

# Slug drift 對照規則

raw 報告檔名常有後綴版本化、wiki 用 canonical 短名。實際對照要剝除以下後綴 / 前綴再比對：

- `-2026` / `-2025` / `-YYYY-MM` 等年月後綴
- `-batch0` / `-batch1.5-v2` 等 batch 後綴
- `-refresh` / `-snapshot` / `-pricing-refresh` 等 stage 後綴
- 日期 prefix `YYYY-MM-DD-` 開頭

範例對照：

| raw slug | 對應 wiki slug |
|---|---|
| `apple-silicon-local-llm-serving-2026` | `apple-silicon-local-llm-serving` |
| `llm-model-landscape-roster-batch0-5` | `llm-model-landscape` |
| `cn-model-anthropic-endpoint-pricing-refresh-2026h2` | `cn-model-swap-landscape` 或 `llm-model-landscape`（已 supersede）|
| `web-fetch-scraping-toolkit` | `web-fetch-scraping-toolkit`（exact match）|

# 前一日候選 follow-up（必做）

讀 `/Users/linhancheng/code/social-info/reports/local-analysis/<today - 1d>-deep-research-pending.md`：

- 抽出昨日「Pending promote 候選」+「不確定 / 待你決定」段所有候選 slug
- 對每個候選查 `~/.claude/wiki/<slug>.md`（用 wiki 的 canonical 短名、考慮 drift）：
  - 檔案存在且 mtime > 昨日報告時間 → 標「✅ promoted YYYY-MM-DD HH:MM」、不再列進今日「Pending promote 候選」
  - 檔案不存在 / mtime < 昨日報告時間 → carry forward 進今日「Pending promote 候選」或「不確定」段（按昨日分類）
- 昨日無報告 → 寫「無前一日報告」
- 昨日有報告但 0 候選 → 寫「前一日無候選」

# 紀律

- 嚴格 read-only：絕對不執行 `/wiki-promote`、不寫任何 memory / wiki / raw 檔
- 只產 markdown 到 stdout
- **第一個 byte 永遠是 `## 掃描範圍`**，不論今日有沒有新候選
- raw 報告內容只讀檔頭抽 TL;DR、不深 drill（避免報告 inflate；deep drill 留給 user 拍板後 `/wiki-promote` 真做）
EOF
)

{
  echo "=== deep-research pending started: $(date) ==="
  "$CLAUDE" -p "$PROMPT" > "$OUT" 2>&1
  size=$(wc -c < "$OUT")
  echo "=== deep-research pending finished: $(date) ==="
  echo "Output: $OUT ($size bytes)"
  if [ "$size" -lt 500 ]; then
    echo "⚠️  WARNING: output < 500 bytes ($size), agent likely short-circuited (e.g. 'skip')"
    echo "--- raw output start ---"
    cat "$OUT"
    echo "--- raw output end ---"
  fi
} >> "$LOG" 2>&1
