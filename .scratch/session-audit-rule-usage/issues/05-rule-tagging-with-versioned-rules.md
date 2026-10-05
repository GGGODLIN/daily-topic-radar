# 05 — 規則標記＋規則版本＋最小狀態

**What to build:** session-audit 分析每段時附上該 session 當時的常駐規則全文（或 03 成立時的索引），模型在既有 findings 之外回報「這段用到哪幾條規則、applied 或 violated、逐字引文」。規則版本每個 session 從 `~/.claude` 的 git 取一次「對話時間前最新的 commit」，並記下用哪個 commit。狀態只存每條規則最後一次遇到場合的時間，以及每週成功分析的段數。

**Blocked by:** 01 — 前置確認：session-audit 已正式啟用；02 — 前置確認：free 池容量實測；03 — 條件票：規則索引退路（僅在 03 成立時）

**Status:** `ready-for-agent`

**Needs:** None — a worker can check every item unaided（假模型 server＋臨時 git repo）

**Validation method:** session-audit 測試以假模型回規則標記，用含兩個 commit 的臨時規則 repo 驗版本選擇，跑 run → status／report

**Evidence required:** 測試輸出；status／report 顯示每條規則的最後遇到時間、每週覆蓋段數與判斷用 commit

**TDD:** `required`

**TDD seam:** session-audit 指令列 run（本機假模型 HTTP server）→ status／report 輸出

- [ ] 舊 session 用它對話時間前的舊版規則判，新 session 用新版 — Source: Story 9
- [ ] 規則範圍含 `CLAUDE.md` 與 `rules/common/` 各檔 — Source: Story 11
- [ ] 回報的每筆規則標記附判斷用的 commit — Source: Requirement: 規則版本取自 git
- [ ] git 取不到版本時，該 session 的規則標記記為未分析並寫 limitation，不改用當前版 — Source: Requirement: 規則版本取自 git — Failure: F4
- [ ] 模型回報不存在的規則、或引文不是原文子字串時丟棄該筆 — Source: Requirement: 同一引擎、同一次讀取 — Failure: F2
- [ ] 模型呼叫失敗或回應格式錯誤的段不算進該週覆蓋 — Source: Requirement: 最小狀態 — Failure: F4
- [ ] 既有 findings 欄位、prompt 的「只做 agent-observation」約束與既有測試結果不變 — Source: Requirement: 同一引擎、同一次讀取
