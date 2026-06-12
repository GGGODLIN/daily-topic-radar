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
OUT="$OUT_DIR/$DATE-wiki.md"
LOG="$LOG_DIR/local-analysis-wiki-$DATE.log"

cd "$REPO_DIR"

PROMPT=$(cat <<'EOF'
你是 wiki 升級候選 audit agent。任務：掃 ~/.claude/memory/（consolidated 大腦，含 work/ projects/ general/ 子目錄）跟 ~/.claude/wiki/ 產一份 markdown 報告。

# 第一原則：永遠輸出完整 6 段 markdown

不論候選數量、不論判準是否全失敗，**第一個 byte 就是 `## 掃描範圍`**。6 段標題逐字照抄、缺一不可：

```
## 掃描範圍
（時間 / 掃了哪些 dir / 共掃過幾個 cluster + 幾個 standalone reference）

## 前一日候選 follow-up
（昨日候選 + 對 wiki/<slug>.md 的查驗結果；無候選寫「前一日無候選」；昨日無報告寫「無前一日報告」）

## 升級候選
（用 `### 候選 N: <名稱>` 格式列；**無候選寫「無新候選」、不省略段標題**）

### 候選 N: <名稱>
- source: <對應 cluster / memory file path>
- mature 訊號: <entry count + 最新 mtime + 為什麼算 mature>
- wiki entity 雛形: <預估 TL;DR + Confidence high/medium/low + Lifecycle verified/reviewed/draft>
- gap 分析: <跟 wiki/ 既有 entity 相關但不同 angle 的列出>

## 不確定 / 待你決定
（滿足 mature 但 entity-vs-discipline 邊界不清的 cluster；**無候選寫「無」、不省略段標題**）

## 已有對應 wiki entity (snapshot only)
（列既有 wiki entity slug + 該 entity 最近 source mtime；幫忙判斷需不需要 refresh）

## 已掃但不夠 mature
（列被判準 1/2 過濾掉的 cluster + 哪條失敗、預估幾天後可重評；幫忙下次跑時 carry forward）
```

**禁止輸出**（理由：歷史報告 5K-12K bytes 是 baseline，短輸出 = short-circuit failure 不是真的無候選）：
- 單行 `skip` / `無候選` / 空檔 / 任何 < 500 bytes 報告
- 整份報告用 code fence 包起來
- Preamble（「以下是 ...」「整理完...」「以下提供...」）
- 略過任何 1 個段標題

歷史報告 5K-12K bytes 是 baseline。今日 < 500 bytes 視為 short-circuit failure。

# Promote-status 標記（最優先，先於下面所有判準）

掃到的 memory file 若 frontmatter（含巢狀 metadata）出現 `wiki_promote:` 欄位，按其值處理、**不進入下面判準流程、不計久懸天數**：

- `declined` / `declined-*`：使用者已拍板不 promote → **完全跳過**，不列進任何段（含「不確定 / 待你決定」）
- `hold-*`（如 `hold-until-trial`）：列進「不確定 / 待你決定」段、標註「⏸ HOLD: <值>」+ 一行 hold 理由，但**不催促拍板、不累計久懸天數**（已有明確解除 trigger）

# 判準（用於 §升級候選 filtering，**不是 short-circuit 條件**）

cluster 同時滿足 4 條才列進「升級候選」段：
1. 同 cluster ≥ 3 entries（或 standalone memory entry ≥ 50 行）
2. 最新 entry mtime ≥ 7 天前（內容已穩定不在演進中）
3. description 偏 entity-centric（指向工具 / 概念 / 系統，不是 incident log / 一次性事件）
4. 對照 ~/.claude/wiki/index.md 該 topic 還沒對應 wiki entity

不滿足全 4 條的 cluster **不漏掉**、改放：

- 滿足 1+2 但不滿足 3（entity-centric 邊界不清）→「不確定 / 待你決定」段
- 滿足 4 但已被覆蓋（refresh 候選）→「已有對應 wiki entity」段
- 1/2 失敗（不夠 mature）→「已掃但不夠 mature」段

「不夠 mature」段是 carry-forward 機制：cluster mtime 4-6 天時列出來、寫「預估 3 天後 ≥ 7d cap 可重評」，下次跑時 follow-up 段可直接抓。

# 前一日候選 follow-up（必做）

讀 /Users/linhancheng/code/social-info/reports/local-analysis/，找昨天日期 (today - 1d) 的 wiki.md：

- 抽出昨日「升級候選」+「不確定 / 待你決定」段所有候選 entity name
- 對每個候選查 ~/.claude/wiki/<slug>.md：
  - 檔案存在且 mtime > 昨日報告時間 → 標「✅ promoted YYYY-MM-DD HH:MM」、不再列進今日「升級候選」
  - 檔案不存在 / mtime < 昨日報告時間 → carry forward 進今日「升級候選」或「不確定」段（按昨日分類）
- 昨日無報告 → 寫「無前一日報告」
- 昨日有報告但 0 候選 → 寫「前一日無候選」

# 紀律

- 嚴格 read-only：絕對不執行 /wiki-promote、不寫任何 memory / wiki file
- 只產 markdown 到 stdout
- **第一個 byte 永遠是 `## 掃描範圍`**，不論今日有沒有新候選
EOF
)

{
  echo "=== wiki candidates started: $(date) ==="
  "$CLAUDE" -p "$PROMPT" > "$OUT" 2>&1
  size=$(wc -c < "$OUT")
  echo "=== wiki candidates finished: $(date) ==="
  echo "Output: $OUT ($size bytes)"
  if [ "$size" -lt 500 ]; then
    echo "⚠️  WARNING: output < 500 bytes ($size), agent likely short-circuited (e.g. 'skip')"
    echo "--- raw output start ---"
    cat "$OUT"
    echo "--- raw output end ---"
  fi
} >> "$LOG" 2>&1
