# 01 — 前置確認：session-audit 已正式啟用

**What to build:** 確認 session-audit 本體已經部署完成並在真實環境跑過：排程已載入、至少一份自然 session 被完整分析、report 指令有真實輸出。這是規則使用量所有實作的起跑條件；答案是「還沒」時，04–09 一律不開工。

**Blocked by:** None — can start immediately

**Status:** `ready-for-agent`

**Needs:** 部署 session-audit 的那個工作已收尾（2026-10-05 另一 session 仍在推修正）；本機 launchd 可查詢

**Validation method:** 查 launchd 服務清單、讀 session-audit state 的完成紀錄、跑 report 指令

**Evidence required:** `launchctl list` 含 session-audit 服務那一行；state 中至少一筆自然 session 的 `latest_complete` 為真；report 指令輸出原文

**TDD:** `waived`

**TDD waiver:** `non-executable-artifact`

**TDD waiver approved:** `ticket-breakdown-user-approved`

- [ ] [Gate] session-audit 服務已載入排程 — Source: Further Notes「前置條件」
- [ ] [Gate] 至少一份自然 session 完整分析完成，report 指令有輸出 — Source: Further Notes「前置條件」
- [ ] [Gate] 若部署仍在進行（有未合併的 session-audit 分支或 worktree），記錄為「未就緒」並停下，不平行改同一檔 — Source: Further Notes「前置條件」
