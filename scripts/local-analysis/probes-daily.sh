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
OUT="$OUT_DIR/$DATE-probes.md"
LOG="$LOG_DIR/local-analysis-probes-$DATE.log"

cd "$REPO_DIR"

PROMPT=$(cat <<'EOF'
你是 social-info repo 的 daily probes runner。讀 `PROBES.md` 的 `## Active` 段對每個 probe 跑 fetch，產出繁中 probe report markdown 到 stdout。

## Step 1 — 讀 PROBES.md

`Read PROBES.md`。看 `## Active` 段。

**若無任何 entry**（只有 `_(空 ...)_` placeholder 或範本而無實際 entry）→ stdout 直接輸出：

```markdown
# Probes Report — <today's date>

無 active probe，跳過。要加 probe 編輯 `PROBES.md` `## Active` 段。
```

然後結束。**不要繼續往下做**。

## Step 2 — 對每個 active probe 跑 fetch

對每個 entry 的 `How to fetch` 跑對應工具：

- `gh release view <repo>` / `gh issue list` / `gh search` 等 → Bash
- WebFetch URL → WebFetch tool
- Context7 SDK doc → mcp__context7__resolve-library-id + query-docs
- claude-in-chrome（需要 cookie / JS）→ mcp__claude-in-chrome__* （見 `~/code/social-info/CLAUDE.md` URL 抓取路由段判斷）
- curl JSON / RSS → Bash curl

## Step 3 — 對照 Hit signal 判斷有沒有新東西

每個 entry 帶 `Hit signal` 描述 baseline（version / 時間戳 / keyword）。比對 fetch 結果：

- **達標（有新東西）**：寫 entry 到 report，含「新訊號內容」+ source link
- **未達標（沒新東西）**：report 寫一行「<title> — no change since <last seen>」
- **fetch fail**：report 寫一行「<title> — fetch failed: <reason>」，不要硬編內容

## Step 4 — 更新 PROBES.md `Last seen` 欄

對每個達標 entry，Edit `PROBES.md` 把該 entry 的 `Last seen` 欄更新為新 baseline 值。

不寫任何其他檔案、不 commit、不修改 `WATCH.md`。

## 輸出格式

```markdown
# Probes Report — <today's date>

## 新訊號 (N)

### <probe title> — <短 highlight>

- **Source**: <URL>
- **新訊號**: <一兩句 — 如 version X.Y.Z released YYYY-MM-DD, breaking change in module Z>
- **Action**: <對應 entry 的 Action on hit>

## 無變化 (N)

- <probe title> — no change since <last seen>

## 失敗 (N)

- <probe title> — fetch failed: <reason>
```

stdout 只輸出 report markdown，不要 preamble、不要結語、不要 code fence wrap。第一個 byte 直接是 `# Probes Report`。
EOF
)

{
  echo "=== probes started: $(date) ==="
  "$CLAUDE" -p "$PROMPT" > "$OUT" 2>&1
  echo "=== probes finished: $(date) ==="
  echo "Output: $OUT ($(wc -c < "$OUT") bytes)"
} >> "$LOG" 2>&1
