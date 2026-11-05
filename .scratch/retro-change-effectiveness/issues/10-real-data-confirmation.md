# 10 — 用真實資料確認

**What to build:** 拿 2026-11-03、2026-11-04 兩輪的真實回顧資料，用新流程重跑 cross 驗收與匯入，寫進累積庫的副本，不動正式累積庫。核對拆成獨立事件後的數量和人工拆分一致，重點是 11-04「搜尋輸出過大」那 10 筆來源觀察。

**Blocked by:** 04 — 匯入時自動寫入觀察

**Status:** `ready-for-agent`

**Needs:**
- 兩輪的 run 目錄與原始 session 紀錄（本機）。
- 正式累積庫的一份副本。

**Validation method:** 一份核對報告。內容包括：重跑指令、副本累積庫的匯入計數、驗收結果、獨立事件的拆分表（人工拆分 vs 程式結果）。

**Evidence required:**
- 指令輸出與計數。
- 拆分表逐筆對照結果。
- 不一致的地方寫出原因。
- 正式累積庫的行數在執行前後相同。

**Tests:** `waived`

**Test waiver:** `non-executable-artifact`

**Test waiver approved:** `ticket-breakdown-user-approved`

- [ ] 兩輪資料用新流程重跑 cross 驗收與匯入，寫進累積庫副本；正式累積庫的行數在執行前後相同 — Source: Requirement: 再犯紀錄（匯入）
- [ ] 11-04「搜尋輸出過大」的 10 筆來源觀察，程式拆出的獨立事件數與人工拆分一致，不一致處寫出原因 — Source: Story 2
- [ ] 兩輪的 cross 輸出逐筆核對通過，或明確列出哪幾筆缺去向或缺理由 — Source: Story 3
- [ ] 舊格式觀察在摘要另外列出，不算進再犯次數 — Source: Requirement: 再犯紀錄（舊紀錄的處理）
