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

4. **memory_entry** — `~/.claude/memory/**/*.md`（consolidated 大腦，2026-05-30 起；舊 `~/.claude/projects/*/memory/` 已凍結不再寫入）
   - `find ~/.claude/memory -name '*.md' -newermt "$(date -v-24H '+%Y-%m-%d %H:%M:%S')"`
   - 對每個 file 用 `head -10` 抓 frontmatter description（fallback basename）；不讀 body
   - **⚠ Hallucination guard（2026-05-21 加入）**：narrative 提到的 memory entry 檔名**必須是 find 實際列出來的真實檔**。不要從 session_prompt / git log subject / 對話內容推測「應該有」但 find 沒撈到的 entry 名稱（例：使用者討論某主題但未沉澱成檔的 topic）。
     - 真實存在 → 寫 `xxx.md` 並列入「新或更新的 memory entry」計數
     - 對話討論未沉澱 → 寫「該段 session 討論 X 主題（未沉澱為 memory entry）」，不要編檔名
     - 違反 = 報告失信，每天都會被 review 抓出來

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

## 規則糾正挖掘（rule-adherence，2026-06-10 起加入；同日 24h 語料校準過）

收集「使用者糾正 Claude 行為」的時刻——上面 retrieval miss 之外的廣義糾正：

- **掃描範圍與 session_prompt 線不同：讀每個 jsonl 的全部 user prompt，不只第一個**。校準實測：24h 內全部糾正事件都發生在 session 中段，只讀第一個 prompt recall = 0。抽取：jq 過濾 `type=="user"` 的 text content（跳過 tool_result、「Base directory for this skill」開頭與 command markdown 注入體、noise regex），每則截 400 字；24h 全量約 100-200 行，直接全部讀
- **不要用關鍵字 grep 當過濾器**。校準實測：窄關鍵字（不對 / 我說過 / 又來了 / 你怎麼又 / 跟你講過）recall 0/6；「不要」13 命中僅 1 真糾正。關鍵字只是提示，必須語意讀全部訊息
- 使用者的糾正大多是**溫和重導向句式**，不是爆氣詞：「先 X 再 Y」（跳步驟被導回）、「先不急著跑，我叫你跑再跑」（越權執行）、「有證據嗎」「不要靠猜測」（無證據推論）、「真的有實作嗎」「跟原文講的是一樣的東西嗎」（宣稱質疑）、「改一下措詞」（定性修正）
- **Skeptic 自審先過再算數**：使用者改需求 / 改主意 / 追加範圍 ≠ 糾正，不收；使用者自己語意模糊後的澄清（「抱歉我是說 X」）、環境問題（vpn / 網路）、單純意圖澄清也不收。模稜兩可的不收（寧漏勿誤報）
- 對每個存活事件分類：
  - 對應得到既有規則（`~/.claude/CLAUDE.md`、`~/.claude/rules/*.md` 的具體條目）→ `rule_violation` + 規則名
  - 對應不到 → `new_rule_candidate`
- **Ledger 寫回（read-only 的唯一例外）**：append 到 `~/code/social-info/reports/local-analysis/rule-adherence-ledger.jsonl`，每事件一行 JSON：`{"date":"<今日>","kind":"rule_violation|new_rule_candidate","rule":"<規則名或空字串>","quote":"<50 字內原文引用>","session":"<jsonl basename>"}`。append 前先 grep ledger 有無同 quote（session 跨日會被掃兩次），有就跳過
- **Rule health**：append 後統計 ledger 內同一 rule 近 30 天出現次數，≥3 → 報告標「⚠ 規則 X 30 天內第 N 次被糾正 → 措辭可能該改（往精確閾值方向）」
- 報告呈現：有命中 → 報告末尾「⚠ 規則糾正: N 次」段，每事件一行（規則名 + 短引用）；`new_rule_candidate` 標明「候選，未驗證——不要自動寫入任何規則檔」；沒有命中 → 完全不列

**輸出報告前的最後動作（必做）**：把本輪全部存活的規則糾正事件 append 進 `rule-adherence-ledger.jsonl`（先 grep 去重）。先寫 ledger、再輸出報告——順序顛倒就會忘。

嚴格 read-only：不寫任何檔案、不 commit、不修改 memory。唯一例外：上述 rule-adherence ledger 的 append。

stdout 只輸出 markdown 報告本身，不要 preamble（「整理完...」「以下是...」）、不要結語、不要 code fence wrap。第一個 byte 直接是 `# Daily Recap` 或數字總覽行。
EOF
)

{
  echo "=== recap started: $(date) ==="
} >> "$LOG"

set +e
"$CLAUDE" -p "$PROMPT" > "$OUT" 2>> "$LOG"
RC=$?
set -e

{
  echo "=== recap claude exit: $RC ==="
  echo "=== recap finished: $(date) ==="
  echo "Output: $OUT ($(wc -c < "$OUT") bytes)"
} >> "$LOG"
