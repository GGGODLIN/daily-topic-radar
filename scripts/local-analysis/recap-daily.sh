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

2. **repo_commit** — `~/Desktop/work/*` + `~/Desktop/projects/*` + `~/code/*` 含 `.git` 的 dir
   - `find ~/Desktop/work ~/Desktop/projects ~/code -maxdepth 2 -name .git -type d 2>/dev/null | xargs -n1 dirname | xargs -n1 -I{} sh -c 'cd {} && pwd -P' | sort -u`
   - **⚠ 為什麼要含 `~/code` + realpath 去重（2026-07-25 加）**：`~/Desktop/projects/social-info` 是指向 `~/code/social-info` 的 **symlink**，`find` 預設不跟隨 symlink → 只掃 Desktop 兩條會整個漏掉這個 repo。加 `~/code` 補實體路徑，再用 `pwd -P` + `sort -u` 去重避免同一 repo 被算兩次
   - 對每個 repo 跑：`git -C <repo> log --branches --since='24 hours ago' --pretty=format:'%H%x1f%cI%x1f%s%x1f%an' --no-merges`
   - **⚠ 必須帶 `--branches`（2026-07-25 加）**：不帶等於只掃當前 HEAD，開在 feature branch 上的 commit 會整批漏掉（2026-07-24 實際漏掉兩筆 akocommerce commit）。用 `--branches`（所有本地 branch）而非 `--all`——後者會把 `origin/*` remote-tracking ref 上同事的 commit 也算進來
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
- **Ledger 寫回（read-only 的第一個例外）**：append 到 `~/code/social-info/reports/local-analysis/rule-adherence-ledger.jsonl`，每事件一行 JSON：`{"date":"<今日>","kind":"rule_violation|new_rule_candidate","rule":"<規則名或空字串>","rule_family":"<家族>","quote":"<50 字內原文引用>","session":"<jsonl basename>"}`。append 前先 grep ledger 有無同 quote（session 跨日會被掃兩次），有就跳過
- **`rule_family` 必填（2026-07-25 加）**：`rule` 欄是自由書寫、同一條規則跨天會分裂成十幾個變體字串，單靠它統計抓不到集中度。從下列固定 enum 挑一個最貼近的填 `rule_family`：`evidence-level`（證據 / 驗證 / 行為數據 / 不憑推測）、`plain-language`（白話 / 反晶晶體 / 措辭 / 繁中）、`self-research-first`（查得到的事實自己查 / 先搜再說缺）、`ask-vs-decide`（停下問 vs 自決 / 討論模式 / 一次收斂 / 訪談）、`scope-discipline`（只改當次範圍 / 修改前先讀）、`process-completeness`（skill 流程不省略 / trial 紀律 / 固化）、`output-delivery`（答案進最終訊息 / 連結格式 / 呈現）、`tooling-routing`（抓取路由 / fetch 退路 / 工具選擇）。都不貼近才填 `unclassified`
- **Rule health**：append 後跑 `python3 ~/code/social-info/scripts/local-analysis/rule-family-health.py`（家族聚合層，優先讀 `rule_family`、缺欄走關鍵字 fallback，舊 entry 無需 migration），取其輸出：
  - 家族 30 天 ≥5 次 → 報告標「⚠ 規則家族 X 30 天內 N 次（占 P%）→ 該家族措辭或 enforce 方式可能該改」
  - 單一 rule 字串 30 天 ≥3 次 → 照舊標「⚠ 規則 X 30 天內第 N 次被糾正 → 措辭可能該改（往精確閾值方向）」
  - 兩層都報，家族層在前——單一字串層看得到「哪句話要改」、家族層才看得到「哪個紀律真的沒守住」
- 報告呈現：有命中 → 報告末尾「⚠ 規則糾正: N 次」段，每事件一行（規則名 + 短引用）；`new_rule_candidate` 標明「候選，未驗證——不要自動寫入任何規則檔」；沒有命中 → 完全不列

## codebase-aliases 候選挖掘（akocommerce 特化，2026-07-09 起加入；跟 rule-adherence 段對稱走 ledger 模式）

Focus：僅 session_prompt 線的 **akocommerce session**（path filter `-Users-linhancheng-Desktop-work-akocommerce`），找對話中反覆用長描述指同一個東西、且沒被 `~/Desktop/work/akocommerce/docs/codebase-aliases.md` 現有 alias 表 cover——「該建 term collapse」候選。

- **掃描範圍**：只讀 akocommerce session（path 含 `-Users-linhancheng-Desktop-work-akocommerce`）、其他 project session 跳過。讀 user + assistant text（跳 tool_result / noise regex），每則截 400 字（跟 rule-adherence 段同校準；assistant text 是 jsonl 最大宗、不截高活動日會爆量）
- **參考 alias 表**：讀 `~/Desktop/work/akocommerce/docs/codebase-aliases.md`（現 226 行、涵蓋 v1-v4 CVS 世代 / 前端頁面 / widget / pickup 等既有代稱）；候選 phrase 若已在表內 alias 或別名欄命中就跳過
- **Signal**：同一個東西被反覆用**長描述**指涉（3+ 字 phrase、講 3 次以上、跨多個 turn）、且 alias 表沒收
- **Skeptic 自審先過再算數**：
  - 自然重述（同 turn 內 rephrase、agent 覆述使用者的話）→ 不算
  - 只講 1-2 次的 phrase → 不算
  - 短 phrase（1-2 字）→ 不算（無 collapse 價值）
  - 純技術詞（不是 domain-specific 概念）→ 不算
  - 模稜兩可 → 寧漏勿誤報
- 每候選抽取：`phrase`（反覆長描述、50 字內）+ `suggested_term`（建議短 term、kebab-case、5-15 字元）+ `session`（jsonl basename）+ `count`（24h 內出現次數）+ `context_snippet`（代表性 quote、80 字內）
- **Ledger 寫回（read-only 的第二個例外）**：append 到 `~/code/social-info/reports/local-analysis/codebase-aliases-candidate-ledger.jsonl`、每候選一行 JSON：`{"date":"<今日>","phrase":"<原文>","suggested_term":"<kebab-case>","count":<int>,"session":"<jsonl basename>","context_snippet":"<80 字內 quote>"}`。**Append 前先 grep ledger 有無同 phrase 或同 suggested_term**（雙鍵去重、跨日避免重複——phrase 是每輪 LLM 挑的代表句、跨日會漂，suggested_term 才是穩定錨點），任一命中就跳過
- **Scan marker（每天必寫、含 0 候選日）**：ledger 每天固定 append 一行 `{"date":"<今日>","kind":"scan_marker","scanned":<akocommerce session 數>,"candidates":<存活候選數>}`——0 候選日也寫，讓「掃了沒中」跟「靜默跳過」在 ledger 上可分辨。Append 前 grep 同日 scan_marker、有就跳過
- **決策回寫（2026-07-16 補、trial review 發現的缺口）**：掃描時順帶偵測既有候選的結局——(a) 候選的 suggested_term 或對應條目已出現在 alias 表 → append `{"date":"<今日>","kind":"decision","suggested_term":"<term>","verdict":"accepted"}`；(b) session 內使用者對某候選明確拒絕（「不用收」「拒絕」「這個不需要」指向該候選）→ append `{"date":"<今日>","kind":"decision","suggested_term":"<term>","verdict":"rejected","reason":"<一句原因、抓得到才寫>"}`。Append 前 grep 同 term 的 decision、有就跳過。**已有 decision 的候選不再重複提報**。沒有這段，採納率（trial 軸 2）永遠算不出來、被拒候選會重複浮上
- 報告呈現：有命中 → 報告末尾「📌 codebase-aliases 候選: N 個 → 見 ledger」段、每候選一行（suggested_term + 短引用）；沒命中 → 完全不列（scan marker 只進 ledger、不進報告）

## Commit outcome 追蹤（2026-07-11 起加入）

repo_commit 線掃描時順帶檢查結局回饋：24h 內 commit subject 以 `Revert` 開頭、或 body 含 "This reverts commit"（用 `git -C <repo> log --since='24 hours ago' --grep='This reverts commit' --pretty=format:'%H%x1f%s%x1f%b'` 補抓）→ 對每個命中用 `git -C <repo> log -1 --pretty=format:'%cI%x1f%s' <被revert的sha>` 查原 commit 日期與 subject。原 commit 是近 14 天內產出 → 報告末尾加「↩️ commit 被 revert」段、每筆一行：`<repo>: <原 subject>（原 commit <日期>）`。目的：把「先前 session 產出、後來被打掉」的結局回饋進 recap，讓後續工作看得到失敗訊號。amend 不追（偵測依賴 reflog、噪音高）。沒有命中 → 完全不列。

**輸出報告前的最後動作（必做）**：把本輪全部存活的規則糾正事件 append 進 `rule-adherence-ledger.jsonl`，並把 codebase-aliases 候選 append 進 `codebase-aliases-candidate-ledger.jsonl`（兩個 ledger 都先 grep 去重）。先寫兩個 ledger、再輸出報告——順序顛倒就會忘。

嚴格 read-only：不寫任何檔案、不 commit、不修改 memory。唯一例外：上述 rule-adherence ledger 跟 codebase-aliases candidate ledger 的 append。

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
