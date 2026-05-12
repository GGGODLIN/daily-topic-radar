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
掃 ~/.claude/projects/-Users-linhancheng-Desktop-projects/memory/ 跟 ~/.claude/wiki/ 找 wiki 升級候選。

判準（必須同時滿足才算成熟）：
1. 同 cluster ≥ 3 entries（或 standalone memory entry 內容 >= 50 行）
2. 最新 entry mtime > 7 天前（內容已穩定，不在演進中）
3. description 偏 entity-centric（指向某個工具 / 概念 / 系統，不是 incident log / 一次性事件）
4. 對照 ~/.claude/wiki/index.md 該 topic 還沒對應 wiki entity

對每個候選列出：
- source: 對應 cluster / memory file path
- mature 訊號: entry count + 最新 mtime + 為什麼算 mature
- wiki entity 雛形: 預估 TL;DR (一句) / Confidence (high/medium/low) / Lifecycle (verified/reviewed/draft)
- 跟 wiki/ 既有 entity 的 gap 分析（如果有相關但不同 angle 的 entity 也列出）

輸出格式：
## 掃描範圍（時間 / 掃了哪些 dir）
## 升級候選
### 候選 N: <名稱>
- source: <...>
- mature 訊號: <...>
- wiki entity 雛形: <...>
- gap 分析: <...>
## 不確定 / 待你決定
## 已有對應 wiki entity（snapshot only）

嚴格 read-only：絕對不執行 /wiki-promote，不寫任何 memory / wiki file。只產 markdown 到 stdout。
EOF
)

{
  echo "=== wiki candidates started: $(date) ==="
  "$CLAUDE" -p "$PROMPT" > "$OUT" 2>&1
  echo "=== wiki candidates finished: $(date) ==="
  echo "Output: $OUT ($(wc -c < "$OUT") bytes)"
} >> "$LOG" 2>&1
