# Rules 檔長度健康度 Weekly — 2026-10-13

**門檻（P-len、harness-audit-2026-07-03 拍板）**：CLAUDE.md ≤ 14000 bytes、rules 檔 ≤ 9728 bytes、scope 總和 ≤ 46080 bytes
**Scope**：~/.claude/CLAUDE.md + rules/common/*.md + rules/external/*.md（不含 MEMORY.md、project CLAUDE.md）

## 🪦 零使用規則候選（3）
連續 4 個有覆蓋的週沒遇到場合；觀察範圍內未見場合的候選，不是已證明的死碼
- 「使用 2 空格縮排」｜識別 `1a2b3c4d5e6f7a8b`｜CLAUDE.md > 全域設定 > 程式碼風格｜判斷用 commit `043d4a26aa11`｜觀察期間 2026-W38～2026-W41（4 週）｜最近一次遇到場合：開始觀察後未見
- 「發現安全問題：停下當前工作先修 CRITICAL；已暴露的 secret 立即輪替；修完掃整個 codebase 同類問題」｜識別 `9f8e7d6c5b4a3921`｜CLAUDE.md > 全域設定 > 工作紀律｜判斷用 commit `043d4a26aa11`｜觀察期間 2026-W38～2026-W41（4 週）｜最近一次遇到場合：開始觀察後未見
- 「如果任務重點是 console、network、performance、Lighthouse、heap 或 isolated browser 診斷，直接使用 chrome-devtools，不先試 Open Browser Use。」｜識別 `0c1d2e3f4a5b6c7d`｜rules/common/browser-routing.md > Browser routing｜判斷用 commit `043d4a26aa11`｜觀察期間 2026-W37～2026-W41（5 週）｜最近一次遇到場合：2026-09-08T10:00:00Z
