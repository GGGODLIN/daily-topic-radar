# 11 — 第一批四項送進待決定清單

**What to build:** 把第一批四項送進正式累積庫的待決定清單，交給使用者決定，系統不自動建立 hook、也不自動撤回。四項是：
- 瀏覽器規則與搜尋規則：要不要做成 hook。
- 契約測試的 PostToolUse 自動提醒，以及 pipestatus hook 連帶擋掉改檔：要不要修或撤。

步驟：
1. 為四個舊改動追加補登紀錄，寫入類型與段落錨點。
2. 兩條規則用段落層級的查詢，重算改動生效後的違反場數。
3. 兩支 hook 照副作用檢查方式，從觸發紀錄抽樣重新檢查。

**Blocked by:**
- 05 — 判定、提議決策、待決定清單
- 06 — 規則違反查詢與管道分工
- 07 — 重犯判斷
- 09 — 三份文件同步，加上第三、四層的操作流程

**Status:** `done`

**Needs:**
- 正式累積庫（只追加）。
- session-audit 資料庫。
- 兩支 hook 的觸發紀錄與審計檔。

**Validation method:** 一份操作報告。內容包括：補登紀錄、兩條規則的段落層級違反場數與原文指針、兩支 hook 的抽樣與判定、待決定清單的實際輸出。

**Evidence required:**
- 累積庫新增的紀錄 id。
- 查詢與抽樣的指令輸出。
- `list --summary` 輸出裡出現的四項。
- 和研究時檔案層數字的差異說明。

**Tests:** `waived`

**Test waiver:** `non-executable-artifact`

**Test waiver approved:** `ticket-breakdown-user-approved`

- [x] 四個舊改動各追加一筆補登紀錄，寫入類型與段落錨點，原改動紀錄不變 — Source: Story 12
- [x] 兩條規則用段落層級查詢重算改動生效後的違反場數；滿 3 場才提出「要不要做成 hook」，並附至少 3 筆原文指針 — Source: Story 9
- [x] 兩支 hook 照副作用檢查方式從觸發紀錄抽樣，結果寫成判定紀錄 — Source: Story 10
- [ ] 四項出現在待決定清單；系統沒有自動建立 hook，也沒有自動撤回 — Source: Story 11
- [x] 重算結果和研究時的檔案層數字不同時，報告寫出差異與原因 — Source: Requirement: 常駐規則被違反時（第一批）

## Verification Log

- 2026-11-06 完成（報告 `.scratch/retro-change-effectiveness/2026-11-06-first-batch-report.md`，不呼叫模型）：正式累積庫 3216 → 3222 行，前 3216 行與備份 `records.jsonl.bak-2026-11-06-before-ticket-11` 逐位元組相同。每筆寫入先 dry-run 再 `--apply`。
- 新增紀錄：第 3217–3220 行補登 `backfill:chg:2026-11-01:browser-routing-lcu-playwright-and-stale-uid:1`（always_on_rule）、`backfill:code-search-exclude-and-runaway-hot-3m-2026-10-31:1`（always_on_rule）、`backfill:chg:2026-11-01:contract-test-posttooluse-additional-context:1`（hook）、`backfill:chg:2026-11-02:pipestatus-gate-unprotected-test-pipe:1`（hook）；第 3221 行 `verdict:chg:2026-11-02:pipestatus-gate-unprotected-test-pipe:1` harmful（第 4 層、副作用已檢查）；第 3222 行 `verdict:chg:2026-11-01:contract-test-posttooluse-additional-context:1` insufficient（第 4 層）。
- 段落層級重算：瀏覽器規則 7 場、26 次（覆蓋 959 場）；搜尋規則 19 場、29 次（覆蓋 1080 場），其中依引文只有 8 場對準這次新增的「排除目錄／TaskStop」子句（7 場是 Argon）。兩條都達 3 場門檻，`list --summary --audit-state` 的 `make_hook` 出現兩項並附原文指針。
- hook 抽樣：pipestatus 47 筆觸發對回 46 筆，46 筆都擋對，其中 16 筆把同一指令裡的改檔或寫檔一起擋掉 → harmful，`open_proposals: [retract, modify]`。契約測試提醒 27 筆抽 15 筆，擋錯 0、連帶擋掉 0，11 筆多餘提醒，4 筆把 agent 引到無關的 wrapper 語系問題（各多約 234～235 秒）。
- 未勾（parked，待使用者決定）「四項出現在待決定清單」：3/4。契約測試提醒逐筆對照過去 10 個事件，改動後 4 次擋不住、改動前 1 次（eb7250c1）可能擋得住，依規則不能判無效，判 insufficient，不進清單。worker 沒有為了進清單改判。
- 與研究數字的差異：改用段落層級（搜尋 29→19 場、瀏覽器 9→7 場）；正式查詢排除 status 仍是 pending 的來源（不過濾時為 8、21 場，接近研究的 8、22 場）；剩 1 場差異沒重現。研究筆記說契約測試提醒針對「逾時」也要更正：使用者批准的原文（累積庫第 2327 行）是「未產生收據時當場提示補跑」，範圍包括測試失敗。
- 規格衝突（待使用者決定）：pipestatus 記成「第 4 層＋幫倒忙」。規格同時寫「連帶擋掉不該擋的動作就是幫倒忙，且優先於有效」與「第四層只能判無效或證據不足」；worker 解讀為後者只限制有效性結論。retro-store `verdict` 不擋這個組合（05、07 已記 Minor）。
- Minor：`--audit-state` 要給 session-audit 目錄，給 `state.sqlite` 檔時所有規則標未檢查、原因只寫 `query-failed`，看不出是路徑錯（main 派工時寫成檔案路徑，是派工說明的錯）。清單為每個 session 附最新一筆違反，搜尋規則段有三個子句，附的 10 筆只有 5 筆對準新子句（段落層級的設計限制）。session-audit 正在背景分析 pending session，場數之後可能變。`~/.claude/hooks/pipestatus-zsh-gate.py` 有其他 session 未提交的修改，補登錨點用第 25 行（原改動紀錄寫 24）。搜尋改動的 runaway.sh script 項目沒有補登也沒有判定。
- 2026-11-06 ticket-yagni：worker 沒有自動登記，手動跑 runner（暫時 worktree 停在 ffc3fa2，base ffc3fa2^）回 `skip: empty-diff`；本票只交付報告與累積庫操作，不重跑。
