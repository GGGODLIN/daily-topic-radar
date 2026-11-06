# 04 — 匯入時自動寫入觀察

**What to build:** 累積庫的 ingest 直接把 cross 輸出的每筆追蹤觀察寫成觀察紀錄，不再靠 main 逐筆執行 observe。

- 結果分四種：無效、沒載入、再犯、未查。無效要附理由；再犯要記涵蓋與否（是／否／不明）。
- 改判用追加的更正紀錄，並連到被取代的那筆觀察。
- 舊格式觀察（沒有事件時間）在摘要另外列出，不算進再犯次數。
- observe 只留給人工補單筆。

**Blocked by:** 03 — cross 逐筆去向＋驗收器逐筆核對

**Status:** `done`

**Needs:** None — a worker can check every item unaided（臨時累積庫與 fixture packet）

**Validation method:** retro-import 與 retro-store 的 CLI 測試；臨時累積庫沿用既有 `StoreCLI` 寫法。

**Evidence required:**
- 測試輸出顯示 ingest 的新增、重播、補證、更正計數。
- 同一 packet 重播新增 0。
- P3 斷言通過。
- 舊格式觀察不在再犯計數裡。
- 現有 retro-store、retro-import 測試全過。

**Tests:** `required`

**Test seam:** retro-import 產生的 packet，以及 retro-store `ingest`、`list` 的輸出與累積庫內容。

**Regression scope:**
- ingest 對問題、發現、事件的既有寫入不變。
- observe、decide、progress、change 的既有行為不變。
- 現有測試全過。
- P3：經 ingest 匯入後，該批事件在累積庫的最新狀態等於 cross 輸出中「保留」「合併後」「改判後」的結果，沒有任何一筆靜默消失。
- 適用輸入：fixture packet，包含四種結果、更正與重播。
- 獨立判準：預期值從 fixture 本身重算。

- [x] ingest 把 cross 每筆追蹤觀察寫成觀察紀錄，不需要手動 observe — Source: Requirement: 再犯紀錄（匯入）
- [x] 事件或 session 早於生效時間的觀察記為無效並附理由；已生效但沒載入的記為「沒載入」；兩者都不算再犯 — Source: Story 1
- [x] 再犯觀察帶涵蓋與否（是／否／不明） — Source: Requirement: 再犯紀錄（涵蓋與否）
- [x] 改判寫成追加的更正紀錄，連到被取代的觀察，舊紀錄內容不變 — Source: Story 3
- [x] ingest 輸出分列新增、重播、補證、更正；同一 packet 重播一次，新增為 0 — Source: Requirement: 再犯紀錄（改判與重播） — Failure: F1
- [x] 沒有事件時間的舊觀察標為舊格式，摘要另外列出，不算進再犯次數，也不把它的時間當成事件時間 — Source: Requirement: 再犯紀錄（舊紀錄的處理） — Failure: F2
- [x] packet 的觀察段缺欄位或型別錯誤時，整批拒收並指出是哪一筆，不寫入部分資料 — Source: Requirement: 再犯紀錄（匯入） — Failure: F2
- [x] observe 仍可人工補單筆，並同樣用事件時間檢查 — Source: Requirement: 再犯紀錄（匯入）

## Verification Log

- 2026-11-06 整合（commit 821e16c）：紅燈基線 retro-store 新測試 5 failures＋6 errors、retro-import 1 fail＋2 errors，原因是功能缺失；綠燈 retro-store `Ran 38 tests ... OK`、retro-import `Ran 12 tests ... OK`、session-retro `Ran 51 tests ... OK`。
- 新紀錄型別：`observation`（id `observation:<observation_key>`，帶 `outcome`、`reported_outcome`、`event_time`、`event_key`、`loaded`、`basis`、`evidence`、`covered`、`cross_disposition`）、`observation_correction`（`supersedes`、`correction_reason`）、`observation_evidence`（補證）。ingest 輸出多 `observations:{new, replayed, supplemented, corrected, rejected}`。list 多 `invalid`、`not_loaded`、`unchecked`、`recurrence_by_coverage`、`legacy`；`--summary` 多 `legacy_observations`。
- Acceptance-miss — 「事件或 session 早於生效時間記為無效」→ worker 只比事件時間，沒處理 session 早於生效（庫裡沒有 session 起始時間）。待送回 worker 補。
- worker 自訂判斷：生效時間以庫裡 change 為準；舊格式＝紀錄沒有 `event_time`；packet 缺 `tracking_dispositions` 照收；cross 剔除的觀察不入庫、只在輸出列出；`covered` 沒給記 `unknown`；同輪同事件重複回報算補證，結果不同就整批拒收；跨輪結果不同要有 reclassified 理由才寫更正。
- Minor：02 之前產生、觀察沒有 `events[]` 的 packet（例如 2026-11-04-full-1）會整批被拒，連問題與發現也不進庫；以前是靜默忽略觀察。10 要先依 03 的發現重跑前段驗收再匯入。
- Minor：人工 observe 新寫的列多了 `event_time`，用同一 id 重打舊的人工 observe 會被當成 conflicting id 拒收。
- Minor：`covered` 上游還沒有欄位，09 要在單場報告契約裡要求模型填涵蓋與否，否則全部是 `unknown`。
- Minor：`not_applicable` 類觀察若事件早於生效或沒載入，也會被改記為 `invalid`／`not_loaded`，原結果留在 `reported_outcome`。
- 2026-11-06 ticket-yagni：keep 15／demote 0／kill 0，沒有要砍的。
- 2026-11-06 補 Acceptance-miss（commit f54f1d3）：用 02 的 `load_time` 對生效時間判斷「session 早於生效」。`not_loaded` 且 basis 為 `last_load_is_old_version`／`load_time_outside_effective_window`、`load_time` 早於生效 → 無效並附理由；`never_loaded` 或 `load_time` 不早於生效 → 沒載入；`load_time` 不是合法時間 → 整批拒收。紅燈 2 個（功能缺失），綠燈 retro-store `Ran 40 tests ... OK`、retro-import 12 OK、session-retro 51 OK。限制：只看 `load_time`，不知道 session 實際開場時間；規則類找不到載入附件時是未查，skill／command 從未載入時是沒載入。
- 2026-11-06 真實資料修正（commit 0cba21e，來源：工作單 10 的 F1、F2、F4、F5、F8、F9）：Acceptance-miss — 「ingest 把 cross 每筆追蹤觀察寫成觀察紀錄」→ 真實 11-04 資料整批被拒。修正後 session-retro `Ran 58 tests ... OK`、retro-store 59 OK、retro-import 14 OK、session-audit-rule-query 17 OK、retro-judge 20 OK。整合時 retro-store.py 指令分派處與 06 的 `rule-anchors` 衝突，兩邊都保留。
  - F1：工具呼叫 ID 的事件，時間與行號一律取工具呼叫那一行。F2：retro-import 只讀 `validated/cross-session.json`，找不到就報錯；run 根目錄與 `reports/` 的舊位置不再讀。F4／F9：三階段驗收擋下 change_id 缺或為 null、以及非 unreviewed 卻沒有結構化 evidence 的追蹤觀察；ingest 把沒有 evidence 的 unreviewed 觀察列在 `unkeyed`。F5：同一事件只差引文時記為補證，補證 id 第二句起加序號。F8：新指令 `retro-store.py tracking-context [--issue ID]`，kind 先取最新 backfill，否則依 `source.path` 推，標 `kind_source`。
  - 11-04 副本重跑（不呼叫模型，正式累積庫仍 3216 行）：reading 驗收 93 過 89，擋下 4 筆缺 change_id 或缺 evidence 的觀察；「搜尋輸出過大」10 筆來源觀察原樣匯入成功，新增 29、補證 7、未進庫 0，重播新增 0；用 `tracking-context` 產生的定義重算後 29 筆全是已載入的再犯（原本全是未查）。29 筆含 F6 待決定的 7 個補救動作。
  - 既有測試 fixture 有改：session-retro 3 處沒有 evidence 的 `not_applicable` 觀察改成 `unreviewed`；retro-import fixture 搬到 `validated/`。原因是契約改了，不是放寬斷言。
  - Minor：46 個真實改動裡 30 個能依路徑推出 kind，16 個推不出（`CLAUDE.md`、`workflows/`、`agents/` 等），載入判斷仍是未查，要靠 backfill。retro-import 不檢查 `validation-cross.json` 是否通過，只讀 validated 那份。
  - ticket-yagni：這次自動審查的範圍錯了（verdict 記錄 worktree＝主 repo、base 6ca8030），60 項 kill 全是 06、07 的程式與其他 session 未提交的 `KNOWN_ISSUES.md`、`PROBES.md`，沒有一項是本次修正。判定無效，不送回 worker，也不重跑。
- 2026-11-06 收尾審查（python-reviewer／typescript-reviewer／security-reviewer，pi-subtask 跨模型 gpt-6.1-sol(high)，8 段）：以下判定成立，main 抽查程式碼確認；套用 final-gate-materiality 後沒有一項可判「成立但不重要」（安全項屬 G4，其餘會寫出錯誤判定或資料且事後看不出，不適用 M2；測試缺口經審查員變異證明測試仍綠，不適用 M4）。處置：修。
  - [HIGH] retro-store.py:936 同一 run 沒寫入列時不留紀錄，重播舊 packet 回滾較新的更正（Acceptance-miss）
  - [HIGH] retro-store.py:1748 tracking-context 的 new_text／source 不取 backfill 錨點，已載入判成沒載入（Acceptance-miss）
  - [MEDIUM] retro-store.test.py:1566 現存 change 屬另一個 issue 的錯配沒有測試（Failure-miss: F2）
