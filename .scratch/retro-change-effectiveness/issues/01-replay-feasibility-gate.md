# 01 — 回放可行性驗證（關卡）

**What to build:** 拿三個已知案例實際回放，回答「回放犯錯那一刻」做不做得到。案例一、二是 gemini-4-argon 在改動生效後犯的錯：瀏覽器沿用舊索引、根目錄搜尋沒排除目錄。案例三是一個改動生效前的原始事件。同時訂出並凍結回放次數、整場測試次數、判定門檻與判斷規則。結論決定 08 回放工具做不做：可行就做；不可行就刪掉 08，文字規則類改從整場測試開始。

**Blocked by:** None — can start immediately

**Status:** `ready-for-agent`

**Needs:**
- 三個案例的原始 session 紀錄（本機）。
- Magpie 中轉站在跑，並有 gemini-4-argon 路由。
- 呼叫模型前，使用者看過成本估算並核准。

**Validation method:** 一份可行性報告，逐案列出重建結果、回放結果與花費，並寫出凍結參數。原型程式用完即丟，不作為正式工具。

**Evidence required:**
- 每個案例的重建比對結果：規則段落以外是否逐字一致。
- 原樣 context 下重現原本違反的次數。
- 舊規則組的結果。
- 實際花費；拿不到的部分標未知。
- 結論（可行／不可行），以及凍結的回放次數、整場測試次數、判定門檻、判斷規則。

**Tests:** `waived`

**Test waiver:** `non-executable-artifact`

**Test waiver approved:** `ticket-breakdown-user-approved`

- [ ] 三個案例都能從原始 session 紀錄重建事件之前的對話，規則段落以外逐字一致 — Source: Story 5
- [ ] 案例一、二用當時原樣的 context（新規則）回放，報告記下重現原本違反的次數，作為「重建是否忠實」的判斷依據 — Source: Requirement: 分層驗證（第一張工作單）
- [ ] 報告記下舊規則組的回放結果 — Source: Requirement: 分層驗證（第一張工作單）
- [ ] 回放使用原 session 的模型、思考深度與路由，gemini-4-argon 走同一個中轉站 — Source: Story 6 — Failure: F3
- [ ] 呼叫模型前先顯示 token 與費用估算，使用者核准後才跑；結束後記錄實際花費，拿不到的標未知 — Source: Story 8 — Failure: F4
- [ ] 中轉站逾時或回錯時記為暫時錯誤並重試，不算成「重現不到」 — Source: Requirement: 分層驗證（暫時性錯誤先重試） — Failure: F4
- [ ] 報告寫出可行或不可行的結論，以及凍結的回放次數、整場測試次數、判定門檻與判斷規則；三個案例只算校準，不作為正式判定 — Source: Requirement: 分層驗證（第一張工作單）

## Verification Log

- 2026-11-06 本批（01–11）開工選擇，真人原話「1a 2a 3a」：
  - 執行方式：Subagent-Driven（每張票派新 worker）。
  - 測試模式：`tdd`，適用 required 的 02–09 共 8 張；01、10、11 為已核准 waived。
  - 01 花費核准：每案原樣 5 次＋換規則 5 次，共 30 次呼叫，估計 US$17–34；實際花費將超過 US$34 時先停下重新估算。
