# 03 — 條件票：規則索引退路

**What to build:** 只在 02 判定「全文不可行」時才做：把規則全文換成規則索引（每條一個編號＋一句摘要），讓模型對照索引回報用到的規則。索引之後要不要、怎麼補送原文，在這張票開工時依 02 的數據設計。02 判定全文可行時刪除本票。

**Blocked by:** 02 — 前置確認：free 池容量實測

**Status:** `ready-for-agent`

**Needs:** 02 的收據與結論

**Validation method:** session-audit 指令列 run 搭配假模型，確認請求內容帶的是索引而非全文、回報的規則編號能對回規則

**Evidence required:** 假模型收到的請求大小低於 02 測得的上限；測試輸出

**TDD:** `required`

**TDD seam:** session-audit 指令列 run（本機假模型 HTTP server）收到的請求內容與 status 輸出

- [x] 02 判定全文可行時，本票刪除、不留半套接線（resolved：02 判定全文可行，本票不實作） — Source: Requirement: 預設送規則全文
- [ ] 請求帶索引時大小不超過 02 測得的上限 — Source: Requirement: 預設送規則全文 — Failure: F6
- [ ] 模型回報的編號不在索引內時丟棄該筆、不計入 — Source: Requirement: 同一引擎、同一次讀取 — Failure: F2

## Verification Log

- 2026-10-05 停用：02 實測判定全文可行（見 02 的 Verification Log），本票不實作、沒有任何接線。票檔保留是為了讓 implement 的決策清單（9 張）對得上；其餘兩條驗收 parked，理由同上。若之後某條 free 腿撐不住全文，再依 02 收據重開。
