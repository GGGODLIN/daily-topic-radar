# 06 — 規則違反查詢與管道分工

**What to build:**
- session-audit 新增只讀查詢：輸入被改段落的位置與原文，以及起始時間；以事件時間過濾，回傳之後違反這一段的不同 session 數、來源指針與覆蓋量。
- 改動生效後同一段落在 3 場以上被違反，就在累積庫的待決定清單提出「要不要做成 hook」，並附原文指針。
- 使用者拒絕後，從拒絕時間重新計算。
- session-audit 把違反寫進摩擦檔之前，先查累積庫的改動錨點，遇到已經做過改動的規則就跳過；其他規則照舊寫入。

**Blocked by:** 05 — 判定、提議決策、待決定清單

**Status:** `done`

**Needs:** None — a worker can check every item unaided（fixture 資料庫與臨時累積庫）

**Validation method:** session-audit 與 retro-store 的 CLI 測試，用 fixture 資料庫涵蓋下列情況。

**Evidence required:**
- 測試輸出顯示查詢回傳的計數、指針與覆蓋量。
- 2 場不提、3 場提出。
- 拒絕後重新計算。
- 摩擦檔跳過已有改動的規則。
- 現有 session-audit 與 retro-store 測試全過。

**Tests:** `required`

**Test seam:** session-audit 新查詢指令與寫入摩擦檔的輸出；retro-store `list --summary` 的待決定清單。

**Regression scope:**
- 沒有對應改動的規則，寫入摩擦檔的行為不變。
- 摩擦檔已存在的條目不搬動。
- session-audit 的標記與零使用報告不變。
- 現有測試全過。

- [x] 查詢輸入段落位置與原文，在 session-audit 內部對應自己的規則身分；累積庫不存 session-audit 的雜湊 id — Source: Requirement: 常駐規則被違反時（只讀查詢）
- [x] 同一標題下其他規則的違反不算進這一段的計數 — Source: Requirement: 常駐規則被違反時（只讀查詢） — Failure: F2
- [x] 以每筆違反的事件時間過濾：拒絕之前開始、拒絕之後才違反的長 session 要算進去 — Source: Story 13 — Failure: F1
- [x] 回傳不同 session 數、每筆來源指針，以及覆蓋量（起始時間後分析了幾場、最近分析到何時）；覆蓋量為 0 時明確顯示「尚未分析」，不顯示「零違反」 — Source: Requirement: 常駐規則被違反時（只讀查詢） — Failure: F4
- [x] 被改段落的原文之後又改過、查不到對應規則時，回報「找不到對應規則」，不回 0 次違反 — Source: Requirement: 常駐規則被違反時（只讀查詢） — Failure: F2
- [x] 改動生效後剛好 3 場違反時提出「要不要做成 hook」，2 場時不提；系統不自動建立 hook — Source: Story 9 — Failure: F6
- [x] 提議附至少 3 筆原文指針 — Source: Requirement: 常駐規則被違反時（附原文指針）
- [x] 使用者拒絕後，以拒絕時間作為新起始時間重新計算，之後又滿 3 場才再提 — Source: Story 13
- [x] session-audit 寫入摩擦檔時，跳過累積庫裡已有改動錨點的規則；其他規則照常寫入；同一條規則不會同時出現在摩擦檔與待決定清單 — Source: Requirement: 常駐規則被違反時（管道分工） — Failure: F5

## Verification Log

- 2026-11-06 完成（commit 062c048、368eba6）：紅燈基線 16 個驗收測試 15 failures＋1 error，原因 `invalid choice: 'rule-violations'／'rule-anchors'`、`unrecognized arguments`、`KeyError: 'make_hook'`；worker 另做 6 種變異（門檻 3 改 2、拿掉事件時間過濾等），各自讓測試失敗。綠燈 session-audit-rule-query `Ran 17 tests ... OK`、session-audit 71 OK、retro-store 50 OK、session-retro 51 OK、retro-import 12 OK；其他 session-audit-*.test.py（throughput 4、replies 10、consume 5、entrypoints 4）全 OK。
- 介面：`session-audit.py rule-violations --state S --rules-repo R --rule-path P --rule-quote Q --since T`，輸出 `status: analyzed|not_analyzed（label 尚未分析）`、`violating_sessions`、`violations[{source_ref, session_id, line, event_time, quote}]`、`coverage{sessions, latest}`；找不到時 `found:false, reason: rule-not-found|rule-ambiguous|rules-unavailable`。retro-store 新增 `rule-anchors`；`list --summary --audit-state S` 的 `pending_decisions` 多 `make_hook`、`make_hook_unchecked`。
- 串法：累積庫只存路徑＋原文；retro-store 摘要以 subprocess 呼叫 session-audit 查詢，起始時間取改動 `effective_at` 與最新 rejected `decided_at` 較晚者；session-audit promote 以 subprocess 呼叫 `retro-store rule-anchors`，有錨點的規則整條跳過。目前只有 `backfill` 且 `kind=always_on_rule` 會被認作規則錨點，新改動要先 backfill。
- ticket-yagni：kill 0／demote 2／keep 28。刪 `RuleAnchorsCommandTest`（分流測試已涵蓋）；`squash` 拿掉，規則原文改成直接比對（整段相等，其次子字串）。
- worker 自訂判斷：規則對應用規則 repo 現行 HEAD，多條規則都含該原文回 `rule-ambiguous`；事件時間取該行時間戳，沒有就取前面最近一筆，讀不到的違反另列 `unresolved_time`；覆蓋量只算載入的規則版本含這一段的 session；讀不到累積庫時 promote 扣住規則條目並回 `rules_withheld`；`--retro-store` 預設指正式累積庫（只讀）。
- Minor：規則段落之後被改寫（含打字修正、搬標題）會讓錨點解析不到，查詢與清單標「找不到」，之後的違反走摩擦檔，要補新的 backfill 錨點才接得回。
- Minor：session-audit promote 每次都以 subprocess 讀一次累積庫（約 5MB）；promote 的執行頻率高時要留意成本，未量測。
- 2026-11-06 收尾審查（python-reviewer／typescript-reviewer／security-reviewer，pi-subtask 跨模型 gpt-6.1-sol(high)，8 段）：以下判定成立，main 抽查程式碼確認；套用 final-gate-materiality 後沒有一項可判「成立但不重要」（安全項屬 G4，其餘會寫出錯誤判定或資料且事後看不出，不適用 M2；測試缺口經審查員變異證明測試仍綠，不適用 M4）。處置：修。
  - [MEDIUM] retro-store.py:1526 make_hook 接受 --change 對象但摘要只查規則錨點（Acceptance-miss）；修法取收窄：make_hook 只收規則錨點
  - [MEDIUM] session-audit.py:3518 覆蓋量以檔案 mtime＋任一舊段落覆蓋判定，時間窗沒分析仍報 analyzed／零違反（Acceptance-miss）
  - [MEDIUM] session-audit.py:3524 覆蓋場數按來源檔計，主對話與子代理重複（Acceptance-miss）
  - 修正：174d720（make_hook 只收規則錨點）、03c2635（覆蓋以「完成規則分析的視窗」事件時間判定、按 session_id 去重）。正式資料只讀重跑瀏覽器規則：違反 7 場不變，覆蓋 959 → 116，與研究筆記同期分析的 Argon 66＋Opus 49＝115 場相近；session-audit-rule-query `Ran 20 tests ... OK`、session-audit 71 OK
