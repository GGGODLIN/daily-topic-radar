# 01 — 回放可行性驗證（關卡）

**What to build:** 拿三個已知案例實際回放，回答「回放犯錯那一刻」做不做得到。案例一、二是 gemini-4-argon 在改動生效後犯的錯：瀏覽器沿用舊索引、根目錄搜尋沒排除目錄。案例三是一個改動生效前的原始事件。同時訂出並凍結回放次數、整場測試次數、判定門檻與判斷規則。結論決定 08 回放工具做不做：可行就做；不可行就刪掉 08，文字規則類改從整場測試開始。

**Blocked by:** None — can start immediately

**Status:** `done`

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
- [x] 案例一、二用當時原樣的 context（新規則）回放，報告記下重現原本違反的次數，作為「重建是否忠實」的判斷依據 — Source: Requirement: 分層驗證（第一張工作單）
- [x] 報告記下舊規則組的回放結果 — Source: Requirement: 分層驗證（第一張工作單）
- [ ] 回放使用原 session 的模型、思考深度與路由，gemini-4-argon 走同一個中轉站 — Source: Story 6 — Failure: F3
- [x] 呼叫模型前先顯示 token 與費用估算，使用者核准後才跑；結束後記錄實際花費，拿不到的標未知 — Source: Story 8 — Failure: F4
- [ ] 中轉站逾時或回錯時記為暫時錯誤並重試，不算成「重現不到」 — Source: Requirement: 分層驗證（暫時性錯誤先重試） — Failure: F4
- [x] 報告寫出可行或不可行的結論，以及凍結的回放次數、整場測試次數、判定門檻與判斷規則；三個案例只算校準，不作為正式判定 — Source: Requirement: 分層驗證（第一張工作單）

## Verification Log

- 2026-11-06 本批（01–11）開工選擇，真人原話「1a 2a 3a」：
  - 執行方式：Subagent-Driven（每張票派新 worker）。
  - 測試模式：`tdd`，適用 required 的 02–09 共 8 張；01、10、11 為已核准 waived。
  - 01 花費核准：每案原樣 5 次＋換規則 5 次，共 30 次呼叫，估計 US$17–34；實際花費將超過 US$34 時先停下重新估算。
- 2026-11-06 完成：結論「可行，但有條件，08 要做」，報告 `.scratch/retro-change-effectiveness/2026-11-06-replay-feasibility-report.md`。30 次正式呼叫全部 HTTP 200；輸入 7,608,682 token（cache_read 3,772,167）、輸出 18,538；牌價推估 US$15.41，兩倍上限價 US$30.81，低於核准的 US$34；實際帳單未知。結論與凍結參數已寫回 spec「分層驗證」第一張工作單段，成本更正寫在 Further Notes。
- 結果：案例一原樣 2/5、舊規則 4/5；案例二原樣 3/5、舊規則 4/5；案例三新舊都是 0/5。
- 未勾（parked）「規則段落以外逐字一致」：新舊兩組請求之間只差目標規則段落（已比對）；但和原請求相比只有 81–95% 的 token（案例一 0.93、二 0.81、三 0.95），原型自己渲染請求，6 項差異列在報告。逐字重建留給 08，做法要使用者決定（見下一條）。
- 未勾（parked）「原模型、思考深度與路由」：模型字串 `google/gemini-4-argon:high` 與路由（Magpie :8317、同一把金鑰）已對上；原 session 記錄的 effort 是 `xhigh`，當時請求有沒有另帶 effort 參數未驗證。
- 未勾（parked）「暫時錯誤重試」：重試邏輯有寫（最多 3 次），但 30 次 0 次暫時錯誤，行為沒有實測到。
- Minor：prompt_snapshot 每場第一筆沒有 tools，第二筆才有；ToolSearch 載入的工具定義記在 `deferred_tools_record` 附件。CC 2.1.316 對這些 session 啟用了對話中段 system 回合、工具變更標頭、寫在對話裡的工具定義三種線上格式，原型沒重現。
- Minor：判斷式 v1 把「先重取狀態、還沒動索引」的 3 次歸成無適用機會，worker 人工補判為照做；凍結規則已寫明這類算照做，08 的判斷式要直接支援。
- Minor：案例三換新規則第 1 次的回應文字裡產生了讀 Gmail 的指令。回放不執行工具；之後若有人拿回放輸出去實際執行，要注意這個風險。
- 2026-11-06 ticket-yagni：worker 沒有自動登記，手動跑 runner（--worktree social-info-wt/t01 --base cbcbc3f）回 `skip: empty-diff`；本票只交付報告，不重跑。
