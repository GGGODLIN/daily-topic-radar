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

**Status:** `ready-for-agent`

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

- [ ] 四個舊改動各追加一筆補登紀錄，寫入類型與段落錨點，原改動紀錄不變 — Source: Story 12
- [ ] 兩條規則用段落層級查詢重算改動生效後的違反場數；滿 3 場才提出「要不要做成 hook」，並附至少 3 筆原文指針 — Source: Story 9
- [ ] 兩支 hook 照副作用檢查方式從觸發紀錄抽樣，結果寫成判定紀錄 — Source: Story 10
- [ ] 四項出現在待決定清單；系統沒有自動建立 hook，也沒有自動撤回 — Source: Story 11
- [ ] 重算結果和研究時的檔案層數字不同時，報告寫出差異與原因 — Source: Requirement: 常駐規則被違反時（第一批）
