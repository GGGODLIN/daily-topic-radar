# 08 — `/rules-slim` 讀零使用段

**What to build:** `/rules-slim` 流程讀到最近一份 rules-size 週報的零使用候選段，並呈現給使用者；不新增「零使用」正式判斷標準，不放寬「2026-08-25 判留的條目不重判」規矩，最終刪改仍逐段由使用者拍板。修改走 skill-creator。

**Blocked by:** 07 — 零使用候選清單：rules-size 週報＋可選欄

**Status:** `ready-for-agent`

**Needs:** None — a worker can check every item unaided

**Validation method:** skill-creator 的 RED → GREEN → REFACTOR 行為驗證

**Evidence required:** skill-creator 驗證收據（改前未呈現零使用段、改後呈現）

**TDD:** `waived`

**TDD waiver:** `non-executable-artifact`

**TDD waiver approved:** `ticket-breakdown-user-approved`

- [x] 跑 `/rules-slim` 時看得到最近一份週報的零使用候選 — Source: Story 3
- [x] 不新增零使用判斷標準、不重判 8/25 判留的條目 — Source: Requirement: `/rules-slim` 只接讀取
- [x] 週報沒有零使用段（舊報告或清單為空）時流程照舊、不報錯 — Source: Requirement: `/rules-slim` 只接讀取 — Failure: F2

## Verification Log

- 2026-10-05 走 skill-creator RED → GREEN → REFACTOR（證據：`../rules-slim-eval/RESULTS.md`；fixture 與改前改後快照因 repo 公開改放本機 trials review-evidence）。RED：兩個情境的 runner 都寫「command 沒規定，我自己判斷」，零使用段處置各自發明（附錄／FYI），未超標報告卡在 Step 1 完成判準。GREEN：同兩情境照新文字處理。REFACTOR：施壓情境（零使用規則在超標檔＋「挑最省事的砍」）與 N=0 回歸都過。`~/.claude` commit bb6ce908；契約測試 `test_skill_verify_one_shot_callers.py` 17 OK。
- Minor（改前就有、非本票）：未超標而使用者仍要跑時「跳到 Step 3」與 Step 3「仍超標才繼續掃」互相矛盾，且跳過 Step 2 會讓 self-verify R2 必判 FAIL。
- Collie 面板：command 名稱與 description 都沒改，不需同步。
