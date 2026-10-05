# 09 — 真實資料確認＋登記 trial

**What to build:** 04–08 完成後，用真實自然 session 跑一輪，核對規則標記、版本收據、skill-up 排除與規則摩擦條目；登記一筆 trial（+7 天看接線，第一段 review 時改 +30 天看第一份零使用清單）。

**Blocked by:** 04 — 排除 skill-up 假對話；05 — 規則標記＋規則版本＋最小狀態；06 — 全域規則摩擦：一條規則一個條目；07 — 零使用候選清單；08 — `/rules-slim` 讀零使用段

**Status:** `ready-for-agent`

**Needs:** session-audit 排程在跑、真實 session、`~/Desktop/projects/.claude/trials/active.md` 可寫

**Validation method:** 讀真實 run 的 status／report 與摩擦檔，逐項比對

**Evidence required:** 真實 run 的 report 原文（含判斷用 commit）、skill-up 排除計數、摩擦檔新增條目原文、trial 的 H2 與 detail 檔路徑

**TDD:** `waived`

**TDD waiver:** `non-executable-artifact`

**TDD waiver approved:** `ticket-breakdown-user-approved`

- [ ] [Confirmation] 真實 session 產出規則標記，report 列出判斷用 commit — Source: Story 9
- [ ] [Confirmation] 真實 run 中 skill-up 對話被排除，計數非零或明寫「本週無 skill-up」 — Source: Story 10
- [ ] [Confirmation] 真實違規以一條規則一個條目寫進待折，標 speculation — Source: Story 4
- [ ] [Confirmation] trial 已登記，review 日為上線 +7 天，detail 寫明第二段 +30 天看零使用清單與停損條件 — Source: Further Notes「觀察分兩段」「停損條件」
