# 05 — 判定、提議決策、待決定清單

**What to build:** 累積庫新增三種追加紀錄，並在摘要裡列出待使用者決定的事。

- 判定紀錄：綁在單一改動上。
- 補登紀錄：舊改動追加類型與段落錨點。
- 提議決策紀錄：記錄使用者對「做成 hook、撤回、修改」這類提議接受或拒絕，拒絕只影響那一個提議。
- 每個改動顯示 `effective`。
- 摘要的待決定清單列出：無效、幫倒忙、需重判的改動，也列出有效改動與它的證據層級，以及只走到第四層的改動數。

**Blocked by:** 04 — 匯入時自動寫入觀察

**Status:** `ready-for-agent`

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

- [ ] 新指令追加判定紀錄，綁在單一改動上，帶判定值、驗證層級、模型與路由、次數、新舊兩組重犯數、證據指針、判定者、副作用是否檢查 — Source: Story 10
- [ ] 同一改動有多筆判定時以最新一筆為準；沒有判定的改動顯示證據不足 — Source: Requirement: 判定與撤回（判定紀錄）
- [ ] `effective` 顯示在每個改動上：有效為 true；無效、幫倒忙為 false；證據不足與未驗證為空值；問題層級不合併成單一值 — Source: Requirement: 判定與撤回（effective 對照）
- [ ] 舊改動可追加補登紀錄，寫入類型與段落錨點，原改動紀錄不變 — Source: Requirement: 再犯紀錄（舊紀錄的處理） — Failure: F2
- [ ] 提議決策紀錄帶提議種類、對象、接受或拒絕、真人原話指針、決定時間；真人原話沿用既有驗證，工具輸出或模型自述不算 — Source: Requirement: 判定與撤回（提議決策）
- [ ] 拒絕一個提議不會改變該問題的批准狀態，後續 progress 與 change 照常可用 — Source: Requirement: 判定與撤回（提議決策） — Failure: F5
- [ ] 判成無效或幫倒忙的改動出現在待決定清單 — Source: Story 11
- [ ] 被拒絕的撤回或修改提議不再列入，除非拒絕時間之後又有新證據判成無效或幫倒忙 — Source: Story 13
- [ ] 摘要列出有效改動與證據層級、需重判、副作用未檢查，以及只走到第四層的改動數 — Source: Requirement: 判定與撤回（判定流程）
