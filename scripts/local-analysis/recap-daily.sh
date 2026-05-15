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
OUT="$OUT_DIR/$DATE-recap.md"
LOG="$LOG_DIR/local-analysis-recap-$DATE.log"

cd "$REPO_DIR"

PROMPT=$(cat <<'EOF'
你是繁中 session recap 整理員。請整理使用者過去 24 小時跨 4 線（CC session / repo commit / ~/.claude 設定 / memory entry）的活動，產出一份繁中 daily recap markdown 到 stdout。

## 4 線 source（用 Bash + Read + Glob 自己 walk）

1. **session_prompt** — `~/.claude/projects/**/*.jsonl`（skip `subagents/`）
   - 找過去 24h mtime 的 jsonl：
     `find ~/.claude/projects -name '*.jsonl' -not -path '*/subagents/*' -newermt "$(date -v-24H '+%Y-%m-%d %H:%M:%S')"`
   - 對每個 jsonl 用 `head -200` 配合 grep `"type":"user"` 找第一個非 noise user prompt
   - NOISE_REGEX（跳過）：`^(<(local-command|command-name|command-message|command-args|system-reminder)|Caveat:|Shell cwd|Stop hook feedback|AUTO-SAVE)`
   - 每筆 prompt content truncate 到 500 chars 再評估，不要一次 dump 整個 jsonl

2. **repo_commit** — `~/Desktop/work/*` + `~/Desktop/projects/*` 含 `.git` 的 dir
   - `find ~/Desktop/work ~/Desktop/projects -maxdepth 2 -name .git -type d 2>/dev/null`
   - 對每個 repo 跑：`git -C <repo> log --since='24 hours ago' --pretty=format:'%H%x1f%cI%x1f%s%x1f%an' --no-merges`
   - 注意：`%x1f` (ASCII Unit Separator) 是欄位分隔符（commit message 可能含 `|`，所以不用 `|`）；`%cI` 是 committer date（不是 `%aI` author date，因為 `--since` 用 committer date filter）
   - `~/Desktop/` 遇到 TCC permission denied 時，明確報告「Desktop TCC 擋」並繼續其他線

3. **claude_config_commit** — `~/.claude/`（單一 repo，不 enumerate）
   - `git -C ~/.claude log --since='24 hours ago' --pretty=format:'%H%x1f%cI%x1f%s%x1f%an' --no-merges`

4. **memory_entry** — `~/.claude/projects/*/memory/*.md`
   - `find ~/.claude/projects -path '*/memory/*.md' -newermt "$(date -v-24H '+%Y-%m-%d %H:%M:%S')"`
   - 對每個 file 用 `head -10` 抓 frontmatter description（fallback basename）；不讀 body

## Sampling 策略（避免 prompt 爆）

- 每筆 session_prompt content 預先 truncate 到 500 chars 再判斷
- commit 只看 subject + author + date，不 `git show` diff
- memory entry 只讀 frontmatter（前 10 行），不讀 body
- 若同線 candidate 超過 30 筆，先列 enumeration（filename + 一行摘要）再選代表性 top 10-15 深讀

## 輸出格式規則

1. 開頭一行數字總覽：N session / N commit / N 個新 memory / 跨 N 個 repo
2. 按日期分組（最新在上）— 過去 24h 通常跨 2 個 calendar day，分開列
3. 每日內按主題分組
4. 標題自帶資訊量（像新聞標題，看標題就懂發生什麼），不要含糊標題後面才展開長 bullet
5. 標題下 1-2 行短敘述
6. 若使用者沒有活動，明確說「沒有活動」

範例好標題：
- 「**新機環境適配**：解決瀏海螢幕 menubar 顯示空間不足、強制 Chrome 同步雲端擴充與配置」

範例壞標題（不要）：
- 「新機環境」（含糊、要展開長 bullet）

## Memory retrieval miss surface（2026-05-14 起加入）

E 規範 trim MEMORY.md 後啟動的觀察：掃 session_prompt 線（使用者 user prompt）內是否出現「retrieval miss」訊號 — 使用者抱怨 Claude 沒找到/沒用到既有 memory 內容：

- 「我之前不是有寫過 X」「找不到 Y」「你怎麼沒用到 reference X」
- 「(memory file name)在哪」「memory 有提到 X 嗎」這類困惑
- 使用者糾正 Claude 重做 research 但其實 memory 已有的場景

若有命中 → 報告末尾單獨加一行 `⚠ retrieval miss: N 次 — 範例：<簡短引用>`；沒有 → 完全不列（保持報告精簡）。

對應觀察 entry：`~/Desktop/projects/.claude/trials/active.md` 內「MEMORY.md E 規範」trial（review 日 2026-06-14）。

嚴格 read-only：不寫任何檔案、不 commit、不修改 memory。

stdout 只輸出 markdown 報告本身，不要 preamble（「整理完...」「以下是...」）、不要結語、不要 code fence wrap。第一個 byte 直接是 `# Daily Recap` 或數字總覽行。
EOF
)

{
  echo "=== recap started: $(date) ==="
  "$CLAUDE" -p "$PROMPT" > "$OUT" 2>&1
  echo "=== recap finished: $(date) ==="
  echo "Output: $OUT ($(wc -c < "$OUT") bytes)"
} >> "$LOG" 2>&1
