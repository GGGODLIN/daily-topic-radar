#!/bin/bash
cd /
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/linhancheng/.local/bin"
export PATH
# headless channel run: 走 hook Defense 0 跳過 nudge 類 Stop hook（checkpoint-judge 曾把最後一則訊息蓋成「skip」、claude -p stdout 只印最後一則，2026-07-15 查因）
export CC_VENDOR=headless-channel

CLAUDE="${SOCIAL_INFO_CLAUDE:-/Users/linhancheng/.local/bin/claude}"
REPO_DIR="${SOCIAL_INFO_REPO_DIR:-/Users/linhancheng/code/social-info}"
OUT_DIR="$REPO_DIR/reports/local-analysis"
LOG_DIR="$REPO_DIR/logs"
PROBES_LOCK="$REPO_DIR/PROBES.md.lock"
LOCK_HELD=0
cleanup_probes_lock() {
  if [ "$LOCK_HELD" -eq 1 ] && [ -d "$PROBES_LOCK" ]; then
    rmdir "$PROBES_LOCK"
    LOCK_HELD=0
  fi
}
trap cleanup_probes_lock EXIT

mkdir -p "$OUT_DIR" "$LOG_DIR"
DATE=$(date +%Y-%m-%d)
OUT="$OUT_DIR/$DATE-probes.md"
LOG="$LOG_DIR/local-analysis-probes-$DATE.log"

cd "$REPO_DIR"

if ! mkdir "$PROBES_LOCK" 2>/dev/null; then
  printf '%s\n' "PROBES writer lock already held: $PROBES_LOCK" >&2
  exit 1
fi
LOCK_HELD=1

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

**不要自己 Edit `PROBES.md`**——寫回一律走 helper，它會做 CAS 比對並把舊 baseline 自動降級進 `Baseline history`，你直接 Edit 會讓歷史鏈斷掉（2026-08-25 起的 prepend 格式，見 PROBES.md「維護規則」）。

對每個達標 entry，組一個 target：`title`（entry 的 `###` 標題原文）、`new_baseline`（**只寫這一輪新增的那一段**、不含 `- **Last seen**: ` prefix、不要抄任何歷史鏈）、`expected_old_prefix`（當下 `Last seen` 的識別值前綴，如 `v2.1.243` 或期號 `409`）。全部 target 放進同一個 JSON 一次送出：

```bash
PROBES_WRITEBACK_LOCK_HELD=1 node ~/.claude/scripts/daily-topic-probes-writeback.mjs <<'JSON'
{"path":"/Users/linhancheng/code/social-info/PROBES.md","targets":[{"title":"...","expected_old_prefix":"...","new_baseline":"..."}]}
JSON
```

`PROBES_WRITEBACK_LOCK_HELD=1` 不可省略——這支 wrapper 在整個 run 期間已經持有 `PROBES.md.lock`，不帶這個變數 helper 會等自己的鎖等到逾時。

helper 回一行 JSON（`succeeded` / `failed` / `evidence`），exit 1 時 stdout 仍是合法 JSON。把它的結果原樣寫進 report，不要自行改寫 counts 或假裝寫回成功。

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
