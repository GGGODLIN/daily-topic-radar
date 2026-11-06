# 05 — 判定、提議決策、待決定清單

**What to build:** 累積庫新增三種追加紀錄，並在摘要裡列出待使用者決定的事。

- 判定紀錄：綁在單一改動上。
- 補登紀錄：舊改動追加類型與段落錨點。
- 提議決策紀錄：記錄使用者對「做成 hook、撤回、修改」這類提議接受或拒絕，拒絕只影響那一個提議。
- 每個改動顯示 `effective`。
- 摘要的待決定清單列出：無效、幫倒忙、需重判的改動，也列出有效改動與它的證據層級，以及只走到第四層的改動數。

**Blocked by:** 04 — 匯入時自動寫入觀察

**Status:** `done`

**Needs:** None — a worker can check every item unaided（臨時累積庫）

**Validation method:** retro-store 的 CLI 測試。

**Evidence required:**
- 測試輸出顯示三種新紀錄的寫入與拒收情況、每個改動的 `effective` 值，以及摘要的待決定清單內容。
- 現有 retro-store 測試全過。

**Tests:** `required`

**Test seam:** retro-store 新指令與 `list`／`list --summary` 的輸出。

**Regression scope:**
- 既有 decide、progress、change、observe 行為不變；問題層級的批准檢查不變。
- 累積庫仍只能追加，同 id 不同內容照舊拒收。
- 現有測試全過。

- [x] 新指令追加判定紀錄，綁在單一改動上，帶判定值、驗證層級、模型與路由、次數、新舊兩組重犯數、證據指針、判定者、副作用是否檢查 — Source: Story 10
- [x] 同一改動有多筆判定時以最新一筆為準；沒有判定的改動顯示證據不足 — Source: Requirement: 判定與撤回（判定紀錄）
- [x] `effective` 顯示在每個改動上：有效為 true；無效、幫倒忙為 false；證據不足與未驗證為空值；問題層級不合併成單一值 — Source: Requirement: 判定與撤回（effective 對照）
- [x] 舊改動可追加補登紀錄，寫入類型與段落錨點，原改動紀錄不變 — Source: Requirement: 再犯紀錄（舊紀錄的處理） — Failure: F2
- [x] 提議決策紀錄帶提議種類、對象、接受或拒絕、真人原話指針、決定時間；真人原話沿用既有驗證，工具輸出或模型自述不算 — Source: Requirement: 判定與撤回（提議決策）
- [x] 拒絕一個提議不會改變該問題的批准狀態，後續 progress 與 change 照常可用 — Source: Requirement: 判定與撤回（提議決策） — Failure: F5
- [x] 判成無效或幫倒忙的改動出現在待決定清單 — Source: Story 11
- [x] 被拒絕的撤回或修改提議不再列入，除非拒絕時間之後又有新證據判成無效或幫倒忙 — Source: Story 13
- [x] 摘要列出有效改動與證據層級、需重判、副作用未檢查，以及只走到第四層的改動數 — Source: Requirement: 判定與撤回（判定流程）

## Verification Log

- 2026-11-06 整合（commit 4c0f871）：紅燈基線 10 個新測試 9 failures＋1 error，原因 `invalid choice: 'verdict'／'backfill'／'proposal'` 與 `KeyError: 'pending_decisions'`；綠燈 retro-store `Ran 50 tests ... OK`、retro-import 12 OK、session-retro 51 OK。
- 新指令：`verdict`（`--verdict effective|ineffective|harmful|insufficient`、`--level 1-4`、`--model`、`--route`、`--runs`、`--old-recurrences`、`--new-recurrences`、證據指針、`--by`、`--side-effects checked|unchecked`、`--at`）、`backfill`（`--kind always_on_rule|skill|command|hook|script`＋段落錨點）、`proposal`（`--kind make_hook|retract|modify`、`--decision accepted|rejected`、對象為 `--change` 或規則錨點，真人原話走 `human_row`）。全部預設 dry-run，`--apply` 才寫入。
- `list --summary` 多 `pending_decisions{ineffective, harmful, needs_rejudge, effective, side_effects_unchecked, level_four_only}` 與 `proposal_decisions`（每個提議種類＋對象的最新一筆，06 讀 `decided_at` 當拒絕時間）。
- worker 自訂判斷：最新判定以追加順序為準；`needs_rejudge`＝判定後又有事件時間晚於判定的再犯觀察；撤回與修改兩種都被拒才移出清單，任一被接受也移出，判定晚於決定就重新列入；副作用未檢查清單只列判有效的改動；規則錨點只驗形狀不驗磁碟原文，比對鍵為路徑＋原文、不含行號；`--model`／`--route`／次數一律必填。
- Minor：第四層只能判無效或證據不足、第一層「只涵蓋已知輸入」、幫倒忙優先於有效，這些對應規則留給 07；目前 `verdict` 指令不擋「第四層判有效」。
- 2026-11-06 ticket-yagni：kill 1／demote 0／keep 17。刪 `list --summary` 的 `proposal_decisions` 摘要鍵（commit 44eda1d）；待決定清單的提議過濾保留，`list --issue` 明細的 `proposal_decisions` 欄位保留。06 要讀拒絕時間時直接讀累積庫的 proposal_decision 紀錄。retro-store `Ran 50 tests ... OK`、retro-import 12 OK。
- 2026-11-06 收尾審查（python-reviewer／typescript-reviewer／security-reviewer，pi-subtask 跨模型 gpt-6.1-sol(high)，8 段）：以下判定成立，main 抽查程式碼確認；套用 final-gate-materiality 後沒有一項可判「成立但不重要」（安全項屬 G4，其餘會寫出錯誤判定或資料且事後看不出，不適用 M2；測試缺口經審查員變異證明測試仍綠，不適用 M4）。處置：修。
  - [MEDIUM] retro-store.test.py:1883 backfill／proposal 預設 dry-run 沒有測試（Acceptance-miss）
  - 修正：174d720（補 backfill／proposal 省略 --apply 不寫入的測試，並以 default=True 變異確認會紅）
