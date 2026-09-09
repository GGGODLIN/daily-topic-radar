# Recap split pilot 固定輸入規格

## 目的與邊界

這份資料是 `/Users/linhancheng/code/social-info/scripts/local-analysis/recap-daily.sh` 的暫時對照輸入。`collector.py` 只收集固定視窗的可追溯資料，不產生 recap，不呼叫模型或 subagent，不寫正式 workflow、帳本、memory、設定，也不讀今天的 recap 或 digest 成品。

來源是 collector 執行當下可讀到的 live files。既有檔案可能已在歷史目標時間之後追加或改寫，無法還原當時 snapshot；本 pilot 不編造完整歷史，只在 `manifest.json` 留下讀取時的 size、mtime、parse error 與變動標記。

## 固定時間條件

- timezone：`Asia/Taipei`
- window：`[2026-09-06T11:10:00+08:00, 2026-09-07T11:10:00+08:00)`
- session 的候選檔只用 `mtime >= window.start` 做預篩，沒有 `mtime` 上限；真正納入必須由 JSONL 內 `user` 或 `assistant` row 的 timestamp 落在上述半開區間決定。
- memory entry 的 `mtime` 本身必須落在上述半開區間。
- 不使用 `date now`、執行當下的相對 24 小時或今天的產出檔。

## 四線輸入

### 1. CC session

- 遍歷 `~/.claude/projects/**/*.jsonl` 的 top-level session 檔，排除任何路徑含 `subagents/` 的檔案；用 wrapper 的 mtime 預篩後逐行解析。
- 只收 `type` 為 `user` 或 `assistant` 且 timestamp 在 window 內的 row。
- 以 `sessionId` 為 session key；缺少時用檔名 stem。若同一 session 出現在多個檔案，合併成一個 session，保留每個來源檔與每筆 line reference。
- `user` 保留所有經固定去噪後的正文，截到 500 個字元。每筆同時保存 `source_chars`、`stored_chars`、`truncated_chars`、`secret_masks`、`source_file`、`line`、原 timestamp。
- `assistant` 不搬 thinking、tool_use 或 tool_result，只保存 window 內最後一段可見 text 的 500 字元 excerpt，欄位 `kind=claim`；這不是已查證事實，也不是模型生成的摘要。
- 已知噪音以固定規則處理：移除 `system-reminder`、local command wrapper、command injection、task notification、`Base directory for this skill` 與 wrapper 明列的 Caveat、Shell cwd、Stop hook feedback、AUTO-SAVE 前綴。`/wait-what` 與 `/wait-what-plus` 保留為 `command_signal`，其他只有 command wrapper 的 row 丟棄。只含 tool_result 的內容不輸出。
- 這些規則只做抽取與去噪，不判定某段是不是使用者糾正；糾正判斷留給下游分析。
- 常見 API key、token、password、Bearer、PEM、GitHub、Slack 與雲端金鑰格式會遮罩為 `<REDACTED>`，每筆與總量都計數。source path、line reference 與 session id 不當成訊息正文。

### 2. repo commit

- 完全沿用 wrapper 的三個 discovery roots：`~/Desktop/work`、`~/Desktop/projects`、`~/code`。
- 只找 maxdepth 2 內的 `.git` directory；以 realpath（`pwd -P` 語意）去重，避免 symlink repo 重算。
- 每個 repo 使用 local `--branches`、`--no-merges`、固定 `--since`/`--until`；不使用 `--all`，不把 remote-tracking branch 的同事 commit 算入。
- 只保存 SHA、committer timestamp、subject、author、repo path，不讀 diff。subject 先做 secret masking。
- 對 `revert` subject 與 `This reverts commit <sha>` 做固定字串訊號抽取，必要時只查被 revert commit 的 timestamp 與 subject；這是下游 commit outcome rubric 的輸入，不直接做報告結論。

### 3. `~/.claude` 設定 commit

- 單獨對 `~/.claude` repo 使用相同固定 window、`--branches`、`--no-merges` 規則。
- 只保存 SHA、committer timestamp、subject、author、repo path，不讀 diff。

### 4. memory entry

- 只掃 `~/.claude/memory/**/*.md`。
- 以固定 window 的 `mtime` 納入；只讀前 10 行中的 frontmatter `description`，找不到就用 basename；不讀 body。
- 每筆保留 path、mtime、description、description line、frontmatter-only 標記與 secret mask 計數。
- 不從 session、commit subject 或對話內容推測不存在的 memory 檔名。

## 下游分析 rubric 保留範圍

這些規則由原 wrapper 定義，但本 collector 只準備證據，不自動判斷或寫回：

- **retrieval miss**：下游可在完整 user prompt 中找「以前寫過、找不到、沒有用到既有 reference、重做已有 memory research」等訊號；沒有命中就不製造段落。
- **rule-adherence**：下游應讀 window 內全部有效 user prompt，不只第一筆；略過 injection 與 tool result。必須先做 skeptic 自審，排除改需求、補充範圍、澄清、環境問題與模稜兩可案例；剩下的才分類為既有 rule violation 或 `new_rule_candidate`。`new_rule_candidate` 必須標成候選、未驗證。
- **rule family**：若下游要分類，只能使用 `evidence-level`、`plain-language`、`self-research-first`、`ask-vs-decide`、`scope-discipline`、`process-completeness`、`output-delivery`、`tooling-routing`、`unclassified`。
- **codebase-aliases**：只對 akocommerce session 與符合 wrapper 補撈條件的 projects session 分析；長描述需出現至少 3 次、跨 turn、不是自然重述、不是短 phrase 或純技術詞，且先對照既有 alias 表。collector 不猜 suggested term，也不寫 candidate ledger。
- **commit outcome**：下游可使用 collector 的 revert signal，只有原 commit 在 revert 前 14 天內才報告；沒有訊號就不加段落。
- **引用要求**：每個 session user claim 必須能回到 `source_file + line + timestamp`；assistant 內容只能以 `kind=claim` 作活動脈絡，不可當作已驗證來源。commit 與 memory 也必須保留 path、SHA 或 mtime 以便回查。

## 固定輸出 schema

- `manifest.json`：schema、window、輸入規則、候選檔 inventory、session/訊息/commit/memory 計數、截斷與遮罩計數、四批分配、輸出 byte 數、不可回溯說明。
- `context.json`：四線中非 session 的 repo commits、`~/.claude` commits、memory frontmatter entries、revert signals；不含 session message，也不含今天 recap/digest。
- `monolithic.json`：所有 session 恰一次，按 `session_id` 排序；包含 session metadata、user messages、最後 assistant claim 與 extraction counters。
- `batches/batch-01.json` 到 `batch-04.json`：每批整個 session，不拆 message。session 先按 serialized session bytes 由大到小、同 bytes 以 session id 排序，再以目前最小批次 bytes 貪婪分配；批次數固定為 4，即使某批為空。
- 每個 JSON 都以 UTF-8、排序 key、固定縮排與結尾換行寫出，讓同一份輸入可以重跑比較 byte。

## 與正式 workflow 的差異

1. 正式 wrapper 用相對 24 小時與 `date`；本 pilot 固定半開時間窗。
2. 正式 wrapper 的 stdout 是模型產生的繁中 markdown；本 pilot 是可追溯 JSON evidence packet，沒有模型摘要。
3. 正式 wrapper 的規則糾正與 alias 結論靠模型 semantic reading；本 pilot 只提供全量有效 user prompt，不替模型決定真糾正。
4. 正式 wrapper 有兩個 ledger append 例外；本 pilot 明確禁止任何正式 ledger 寫入。
5. 正式 wrapper 的報告會把 activity、retrieval miss、rule health 與 revert outcome 混成 narrative；本 pilot 將 session 與四線 metadata 分離，保留下游所需的原始引用。
6. 正式 wrapper 可能讀取 alias 表與 rule-family health；本 pilot 不把那些正式檔案當輸入，避免把既有分析結果反餵成活動證據。

## 驗證要求

`collector.py --self-test` 的人工 fixture 必須確認：window 外 row 被排除、system-reminder 只含噪音時被排除、同一 session 不重複、四批恰好存在且 session 集合互斥完備、每一筆輸出的 line reference 能回到 fixture source。真實執行後另以 JSON parser 驗證 monolithic 與四批的 session union、intersection、line bounds 與固定 window。
