# 03 — 整合驗證並提交

**What to build:** 以最窄的整合驗證確認 pin watcher 與 trial ledger 都符合 spec，保留可重跑證據，並把兩個 repository 的變更各自提交，不夾帶既有未提交內容。

**Blocked by:** 01 — 實作 Claude Code pin watcher；02 — 登錄七天觀察 trial

**Status:** ready-for-agent

**Needs:** GitHub CLI 與網路可用，以執行一次真實 read-only probe。

**TDD:** waived

**TDD waiver:** non-executable-artifact

**TDD waiver approved:** ticket-breakdown-user-approved

- [x] **Confirmation:** tool-updates 既有單一測試檔全綠。
- [x] **Confirmation:** trial ledger 檔首列出的三個契約測試全綠，且斷言覆蓋新 entry 的 H2／三欄索引／detail identity。
- [x] **Confirmation:** 真實 read-only probe 不把目前仍未確認修復的版本誤報成可解除 pin。
- [x] **Confirmation:** social-info diff 不包含既有 KNOWN_ISSUES.md 變更或其他無關檔案。
- [x] **Confirmation:** trial ledger diff 只包含新 H2 與對應 detail 檔。
- [x] social-info 與 trial ledger 各自使用 Conventional Commit 留存；只 push 使用者已授權的個人 repository。
- [x] 回報未驗證的邊界：當前 session 無法自行證明未來候選版本的 fullscreen sticky prompt 行為。

## Verification Log

- V2 focused：`cc-tool-updates-daily.test.sh` 最終輸出測 7–12 全綠並以 `ALL PASS` 結束；review 修正另以 RED 重現尾端空 heading crash 與第六筆候選 release 漏報，再各自轉綠。
- V4 repository：在 repo cwd 執行 `uv run pytest`，結果 `167 passed in 1.89s`。前兩次因 cwd 設定錯誤未構成功能失敗，沒有修改或放寬測試。
- Trial contracts：archive helper 45 checks PASS、review friction contract 72 PASS、ledger append contract PASS；額外 identity probe 確認唯一 H2、detail pointer 與 detail 前兩行一致。
- Live read-only probe：`pin_updates=[]`、`pin_errors=[]`，證明 2026-08-29 當時未達提醒條件；不證明候選版本的 TUI 行為。
- TDD policy：manifest `/Users/linhancheng/.claude/logs/matt-tdd/cf4f5877-76aa-4bbc-bbd9-1ed35c72ddc0/call_IvzVcU7YK77aq6Tv1Y0SX8J3/decision.json` 回 `valid=true`、1 張 required／3 張 tickets。
- Review：兩位 reviewer 共確認兩個核心缺陷；尾端空 heading crash 與 top-5 窗口漏報都已用 focused RED→GREEN 修正。其餘 heading 格式漂移、無 candidate 測試缺口、編號前綴與重複 notes 保留為已知限制。
- Commit scope：trial repo commit `716c1a1` 只含 active H2 與 detail；social-info feature stage 排除既有 `KNOWN_ISSUES.md`。
