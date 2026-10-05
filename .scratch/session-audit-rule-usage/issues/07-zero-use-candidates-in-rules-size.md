# 07 — 零使用候選清單：rules-size 週報＋可選欄

**What to build:** 以完整規則名冊對照 05 的最小狀態，找出「連續 4 個有覆蓋的週都沒遇到場合」的規則，寫成 rules-size 週報的一段；每條附規則原文與識別、判斷用 commit、觀察期間、最近一次遇到場合的時間或「開始觀察後未見」，措辭是「觀察範圍內未見場合的候選」。daily-local 第 12 條的可選欄在清單非空時多一行「🪦 零使用規則 N 條 → /rules-slim」。

**Blocked by:** 05 — 規則標記＋規則版本＋最小狀態

**Status:** `ready-for-agent`

**Needs:** None — a worker can check every item unaided

**Validation method:** 對清單產出指令餵構造好的多週狀態；daily-local hook 文字測試加斷言

**Evidence required:** 清單產出測試輸出；`daily-local-analysis-trigger.test.sh` 新斷言通過

**TDD:** `required`

**TDD seam:** 零使用清單產出指令（session-audit 子指令或 rules-size wrapper）的文字輸出，以及 daily-local hook 注入文字

- [ ] 連續 4 個有覆蓋的週沒遇到場合的規則列入 — Source: Story 7
- [ ] 從未被模型回報過的規則也會依名冊列入 — Source: Story 6
- [ ] 違規過的規則不列入零使用 — Source: Story 6
- [ ] 中間有一週成功段數為 0，連續計數重新起算，不拼週 — Source: Requirement: 零使用清單 — Failure: F6
- [ ] 規則原文改寫後視為新規則，從頭計數 — Source: Requirement: 規則識別
- [ ] 每條附判斷用 commit、觀察期間與最近遇到時間 — Source: Requirement: 零使用清單
- [ ] 清單為空時可選欄不列這一行 — Source: Story 8 — Failure: F6
- [ ] 可選欄這一行指向 `/rules-slim`，且不新增 channel、提醒或排程 — Source: Story 8
