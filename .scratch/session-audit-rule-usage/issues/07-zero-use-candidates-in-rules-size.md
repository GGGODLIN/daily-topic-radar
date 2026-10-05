# 07 — 零使用候選清單：rules-size 週報＋可選欄

**What to build:** 以完整規則名冊對照 05 的最小狀態，找出「連續 4 個有覆蓋的週都沒遇到場合」的規則，寫成 rules-size 週報的一段；每條附規則原文與識別、判斷用 commit、觀察期間、最近一次遇到場合的時間或「開始觀察後未見」，措辭是「觀察範圍內未見場合的候選」。daily-local 第 12 條的可選欄在清單非空時多一行「🪦 零使用規則 N 條 → /rules-slim」。

**Blocked by:** 05 — 規則標記＋規則版本＋最小狀態

**Status:** `done`

**Needs:** None — a worker can check every item unaided

**Validation method:** 對清單產出指令餵構造好的多週狀態；daily-local hook 文字測試加斷言

**Evidence required:** 清單產出測試輸出；`daily-local-analysis-trigger.test.sh` 新斷言通過

**TDD:** `required`

**TDD seam:** 零使用清單產出指令（session-audit 子指令或 rules-size wrapper）的文字輸出，以及 daily-local hook 注入文字

- [x] 連續 4 個有覆蓋的週沒遇到場合的規則列入 — Source: Story 7
- [x] 從未被模型回報過的規則也會依名冊列入 — Source: Story 6
- [x] 違規過的規則不列入零使用 — Source: Story 6
- [x] 中間有一週成功段數為 0，連續計數重新起算，不拼週 — Source: Requirement: 零使用清單 — Failure: F6
- [x] 規則原文改寫後視為新規則，從頭計數 — Source: Requirement: 規則識別
- [x] 每條附判斷用 commit、觀察期間與最近遇到時間 — Source: Requirement: 零使用清單
- [x] 清單為空時可選欄不列這一行 — Source: Story 8 — Failure: F6
- [x] 可選欄這一行指向 `/rules-slim`，且不新增 channel、提醒或排程 — Source: Story 8

## Verification Log

- 2026-10-05 分兩半。session-audit／rules-size 半由 worker（routed-impl）做：patch sha256 0a69f36c…，main 重算一致，base 4ecc774，疊在 06 之後 `git apply --check` 通過。RED：4 條新測試因 `zero-use` 子指令不存在而紅；`--now` 那條用暫時拿掉未來週過濾確認會紅；wrapper 測試 RED 時 10 過 12 敗（沒有零使用段、沒有 `__SILENT__` 判斷）。GREEN（main 重跑）：`session-audit.test.py` OK、regressions OK、entrypoints OK、`rules-size-weekly.test.sh` 22/0。
- hook 半由 main 做（`~/.claude` commit 043d4a26）：`daily-local-analysis-trigger.test.sh` 新增 5 條斷言；用改動前的 hook 跑 → 5 FAIL（pass=74 fail=5），改動後 pass=79 fail=0。這半是先改 hook 才補測試，RED 是事後用舊版重放觀察到的。
- main 唯讀 smoke：`zero-use` 對現役 state 輸出「## 🪦 零使用規則候選（0）／本週沒有候選」，exit 0（現役 state 還沒有帶規則的 run）。
- 已接受的 worker 判斷：`--now` 之後的週不計（防未來 timestamp 把清單無聲清空）；zero-use 失敗時即使未超標也寫報告並記失敗行（不靜默）。
- 釐清：「違規過的規則不列入」依 spec「連續 4 個有覆蓋的週沒遇到場合」解讀為「違規落在這段連續區間內就不列」；更早以前違規、之後連續 4 週安靜的規則仍會列入。
- worker 自報：RED 時 wrapper 尚未有路徑覆寫，曾寫入真實 `reports/local-analysis/2026-10-05-rules-size.md` 與對應 log（皆 gitignored），已刪除；main 確認該檔不存在（計數 0）。
- Minor：舊 commit 在規則 repo 讀不到時該週全當沒覆蓋、HEAD 名冊讀不到時 exit 2，兩條沒有專門測試。
- 2026-10-05 ticket-yagni verdict（kill 0／demote 80／keep 17）：77 條 demote 的對象是 base 既有、本票沒改的函式（審查把整檔符號都列入），不需處理。其餘 3 條都**不接受**：`cleanup`、`pass` 是 `rules-size-weekly.test.sh` 自身的測試輔助，拿掉測試就跑不動；`redact_text` 是零使用條目寫進週報前沿用的既有淨化，與其他報告一致。
- 2026-10-05 implement 收尾 code review。本票相關：
  - Failure-miss: F5 — zero-use 失敗行沒有 ⚠️，下游接不到。修：失敗行以 `⚠️ ` 開頭（rules-size 測試 23/0）。
  - Failure-miss: F5 — 只有零使用段的 rules-size 報告會被排進高中低／ledger。修：daily-local 第 12 條加「零使用段只進可選欄，不進三檔、不進沉底行、不寫 ledger；只有零使用段時整份 rules-size 不排檔」（`~/.claude` ba77f12b，hook 測試 80/0，新斷言改前紅）。
