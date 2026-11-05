# 04 — 匯入時自動寫入觀察

**What to build:** 累積庫的 ingest 直接把 cross 輸出的每筆追蹤觀察寫成觀察紀錄，不再靠 main 逐筆執行 observe。

- 結果分四種：無效、沒載入、再犯、未查。無效要附理由；再犯要記涵蓋與否（是／否／不明）。
- 改判用追加的更正紀錄，並連到被取代的那筆觀察。
- 舊格式觀察（沒有事件時間）在摘要另外列出，不算進再犯次數。
- observe 只留給人工補單筆。

**Blocked by:** 03 — cross 逐筆去向＋驗收器逐筆核對

**Status:** `ready-for-agent`

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

- [ ] ingest 把 cross 每筆追蹤觀察寫成觀察紀錄，不需要手動 observe — Source: Requirement: 再犯紀錄（匯入）
- [ ] 事件或 session 早於生效時間的觀察記為無效並附理由；已生效但沒載入的記為「沒載入」；兩者都不算再犯 — Source: Story 1
- [ ] 再犯觀察帶涵蓋與否（是／否／不明） — Source: Requirement: 再犯紀錄（涵蓋與否）
- [ ] 改判寫成追加的更正紀錄，連到被取代的觀察，舊紀錄內容不變 — Source: Story 3
- [ ] ingest 輸出分列新增、重播、補證、更正；同一 packet 重播一次，新增為 0 — Source: Requirement: 再犯紀錄（改判與重播） — Failure: F1
- [ ] 沒有事件時間的舊觀察標為舊格式，摘要另外列出，不算進再犯次數，也不把它的時間當成事件時間 — Source: Requirement: 再犯紀錄（舊紀錄的處理） — Failure: F2
- [ ] packet 的觀察段缺欄位或型別錯誤時，整批拒收並指出是哪一筆，不寫入部分資料 — Source: Requirement: 再犯紀錄（匯入） — Failure: F2
- [ ] observe 仍可人工補單筆，並同樣用事件時間檢查 — Source: Requirement: 再犯紀錄（匯入）
