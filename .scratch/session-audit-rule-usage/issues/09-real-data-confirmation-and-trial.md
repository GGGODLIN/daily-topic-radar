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

- [x] [Confirmation] 真實 session 產出規則標記，report 列出判斷用 commit — Source: Story 9
- [x] [Confirmation] 真實 run 中 skill-up 對話被排除，計數非零或明寫「本週無 skill-up」 — Source: Story 10
- [ ] [Confirmation] 真實違規以一條規則一個條目寫進待折，標 speculation — Source: Story 4
- [x] [Confirmation] trial 已登記，review 日為上線 +7 天，detail 寫明第二段 +30 天看零使用清單與停損條件 — Source: Further Notes「觀察分兩段」「停損條件」

## Verification Log

- 2026-10-05 真實確認（證據：`../real-confirmation/`；本 repo 公開，含真實對話引文的 report 全文與摩擦 diff 不入庫，只留規則統計行 `report-rules-lines.txt` 與計數）。做法：獨立 state 目錄、真實 free 池、真實 `~/.claude` 規則 repo；來源是 4 份真實 CLI 對話（79a4fad3、c325baf0 各約 56 KB 可分析內容，97a3dc38、79a70ff4 較短）＋1 份真實 skill-up 對話；promote 寫到真實摩擦檔的副本，不碰正式摩擦檔與正式 state。腳本 `real-confirmation/run.sh`。
  - 規則標記：`weekly_segments` 2026-W40 = 10 段；`last_seen` 6 條（皆 applied，CLAUDE.md 的工作紀律／連結格式／語言偏好／工作模式）。report 每個 session 列出判斷用 commit `0820de2e`；main 用 `git log -1 --before=<對話時間> -- CLAUDE.md rules/common` 獨立重查兩個時間點，結果一致。
  - skill-up 排除：1 份 skill-up 對話 classification=synthetic、included=false，沒有送模型。
  - 違規 → 摩擦條目：**未觀察到**。這批樣本模型回報 0 筆 violated，promote 只寫了 3 條既有類型的 session candidate（`friction-promote-count.txt`），沒有規則條目。此項 parked：行為由 06 的 9 條測試覆蓋；真實資料要等觀察期第一段 review（2026-10-12）看正式排程。
  - trial：`~/Desktop/projects/.claude/trials/active.md` 的 `## session-audit-rule-usage (2026-10-12)`，detail `trials/active/session-audit-rule-usage-2026-10-05.md`（寫明 +30 天第二段與停損條件），commit e50b932。
- Failure-miss: F4 — free 池讀逾時／斷線 → `post_json` 只接 HTTPError／URLError，`TimeoutError`／`RemoteDisconnected` 讓整輪 run 當掉（正式 error log 自 19:47 起 4 筆）。main 在 09 確認時發現並修正（commit 5e10a60，測試 `test_upstream_drop_does_not_crash_run` 先紅後綠）；真實確認中 24 次 `relay-dropped` 都被接住、run 沒有當掉。
- Minor：本輪約 37 次 run 有 24 次斷線，比例高；斷線段下輪重試，不會誤記成功，但會拖慢正式排程的吞吐。列入 trial 第一段觀察。
- Minor：6 條 last_seen 的時間都等於該 session 最早 timestamp（已知限制：週別與 last_seen 取 session 起點）。
