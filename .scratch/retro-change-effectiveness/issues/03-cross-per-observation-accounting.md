# 03 — cross 逐筆去向＋驗收器逐筆核對

**What to build:** cross 階段不再每組只留一筆代表觀察。每一筆輸入觀察都要有去向：保留、合併到同一事件、改判、剔除，其中改判和剔除要附理由。驗收器逐筆核對，少一筆或缺理由就不通過，並指出是哪一筆。

**Blocked by:** 02 — 事件與「有沒有載入新版」的共用判斷

**Status:** `ready-for-agent`

**Needs:** None — a worker can check every item unaided（去敏固定案例）

**Validation method:**
- session-retro 的 cross 驗收測試，用去敏固定案例；案例形狀照 2026-11-03、11-04 兩輪的 cross 報告結構。
- retro-workflow 契約測試，檢查 cross 階段的提示文字。

**Evidence required:**
- 測試輸出顯示 P1、P2 斷言通過。
- 缺去向、缺理由兩種反例都被拒，錯誤訊息指出是哪一筆。
- 現有 session-retro 與 retro-workflow 契約測試全過。

**Tests:** `required`

**Test seam:** session-retro `validate --stage cross` 的輸出；cross 流程腳本的契約測試。

**Regression scope:**
- reading、session-merge 階段的驗收行為不變。
- findings 與 dispositions 的既有核對不變。
- 現有測試全過。
- P1：每一筆輸入觀察（事件＋改動識別）在輸出恰好有一個去向。
- P2：輸出結果與輸入不同時，理由不得為空。
- 適用輸入：去敏固定案例，包含重複事件、四種去向、合併與改判。
- 獨立判準：預期值由測試從案例本身重算，不呼叫驗收器的程式，也不沿用研究筆記的數字。

- [ ] cross 流程腳本的契約文字改為「每筆輸入觀察都有去向」，列出四種去向 — Source: Requirement: 再犯紀錄（cross 契約）
- [ ] 任一輸入觀察在輸出找不到去向時驗收不通過，錯誤指出事件鍵與改動識別 — Source: Story 2 — Failure: F4
- [ ] 改判或剔除沒有理由時驗收不通過 — Source: Story 3
- [ ] 只有事件鍵相同的觀察可以合併；不同事件即使屬於同一條規則也不能合併 — Source: Requirement: 再犯紀錄（事件與觀察分開定義）
- [ ] reading、session-merge、cross 三個階段都用 02 的同一個事件鍵 — Source: Requirement: 再犯紀錄（事件與觀察分開定義） — Failure: F5
- [ ] 舊格式的 cross 報告（沒有逐筆去向）在驗收時明確報為舊格式，不判通過 — Source: Requirement: 再犯紀錄（驗收器逐筆核對） — Failure: F2
- [ ] 固定案例的 P1、P2 斷言通過，預期值由測試從案例本身重算 — Source: Requirement: 再犯紀錄（驗收器逐筆核對）
