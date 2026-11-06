# 10 — 用真實資料確認

**What to build:** 拿 2026-11-03、2026-11-04 兩輪的真實回顧資料，用新流程重跑 cross 驗收與匯入，寫進累積庫的副本，不動正式累積庫。核對拆成獨立事件後的數量和人工拆分一致，重點是 11-04「搜尋輸出過大」那 10 筆來源觀察。

**Blocked by:** 04 — 匯入時自動寫入觀察

**Status:** `done`

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
- [x] 兩輪的 cross 輸出逐筆核對通過，或明確列出哪幾筆缺去向或缺理由 — Source: Story 3
- [x] 舊格式觀察在摘要另外列出，不算進再犯次數 — Source: Requirement: 再犯紀錄（舊紀錄的處理）

## Verification Log

- 2026-11-06 第一次核對（報告 `.scratch/retro-change-effectiveness/2026-11-06-real-data-confirmation-report.md`，不呼叫模型）：正式累積庫前後都是 3216 行、5392255 bytes；原 run 目錄 0 個檔案被改。
- 驗收 1 未過：reading、session-merge 兩輪都通過（11-03 75/75、12/12；11-04 93/93、14/14）；cross 兩輪都判舊格式（11-03 368 筆、11-04 417 筆沒有逐筆去向）；ingest 兩輪整批拒收（`events must match evidence one to one`）。新格式 cross 輸出要重跑模型，依工作單未做。
- 驗收 2 未過：11-04「搜尋輸出過大」10 筆觀察共 36 行證據，人工拆 22 個事件，程式拆 29 個。多出的 7 個全是補救或追查動作（TaskStop 3、縮小範圍重查 3、讀過大輸出存檔 1），各有自己的工具呼叫 ID。
- 驗收 3 未過但已列出缺漏：11-03 缺去向 368、輸出找不到 290、改結果沒理由 1、證據是自由文字 4；11-04 缺去向 417、輸出找不到 373、證據是自由文字 2、同事件沒合併 3。逐筆清單在 gitignore 的 `reports/local-analysis/retro-replay/ticket-10/logs/`。
- 驗收 4 通過：副本摘要 `legacy_observations: 212`；「搜尋輸出過大」`legacy: 7, recurrence: 0`。
- 發現的程式問題（重現指令在報告）：F1 同一事件的事件時間取自被引用的那一行，呼叫行與結果行時間不同時 ingest 判衝突、整批拒收；F2 retro-import 讀模型原稿 `reports/cross-session.json`，`events[]` 只寫在 `validated/`（讀程式碼推得，未用新格式實跑）；F3 validate 找不到 transcript 時沿用舊的通過結果，新檢查沒跑（2026-10-20／10-28 既有行為）；F4 觀察沒有 evidence 或 change_id 為 null 時 ingest 整批拒收（11-03 有 16 筆、11-04 有 6 筆）；F5 同一行不同引文被判衝突、整批拒收；F6 補救動作被算成獨立事件；F7 分叉 session 共用工具呼叫 ID，同一事件出現在兩份單場報告且結論衝突；F8 `tracking-context.json` 缺 `kind`／`effective_time`／`new_text`／`name`，載入判斷全是未查；F9 change_id 為 null 時事件鍵變成 `…|None`。
- 狀態：不關票。F1、F2、F4、F5、F8、F9 修正後重跑本票核對；F6 待使用者決定。
- 2026-11-06 使用者「照建議」：新格式 cross 的完整一輪留到下一次真實 /retro 時驗，不另花模型費用重跑。驗收 1（新流程重跑 cross 與匯入）parked：延到下一輪真實執行；匯入段已用診斷資料在修正後確認（見 04 的 Verification Log：0cba21e 後 11-04「搜尋輸出過大」原樣匯入成功）。驗收 2（事件數與人工拆分一致）parked：依同一批決定，證據角色（action／context）要由單場報告標出，舊報告沒有角色標記算舊格式，只能等下一輪真實執行產生新格式報告後核對。
- 2026-11-06 ticket-yagni：worker 沒有自動登記，手動跑 runner（暫時 worktree 停在 6ca8030，base 6ca8030^）回 `skip: empty-diff`；本票只交付報告，不重跑。
