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

- [ ] 跑 `/rules-slim` 時看得到最近一份週報的零使用候選 — Source: Story 3
- [ ] 不新增零使用判斷標準、不重判 8/25 判留的條目 — Source: Requirement: `/rules-slim` 只接讀取
- [ ] 週報沒有零使用段（舊報告或清單為空）時流程照舊、不報錯 — Source: Requirement: `/rules-slim` 只接讀取 — Failure: F2
