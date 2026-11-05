# 06 — 規則違反查詢與管道分工

**What to build:**
- session-audit 新增只讀查詢：輸入被改段落的位置與原文，以及起始時間；以事件時間過濾，回傳之後違反這一段的不同 session 數、來源指針與覆蓋量。
- 改動生效後同一段落在 3 場以上被違反，就在累積庫的待決定清單提出「要不要做成 hook」，並附原文指針。
- 使用者拒絕後，從拒絕時間重新計算。
- session-audit 把違反寫進摩擦檔之前，先查累積庫的改動錨點，遇到已經做過改動的規則就跳過；其他規則照舊寫入。

**Blocked by:** 05 — 判定、提議決策、待決定清單

**Status:** `ready-for-agent`

**Needs:** None — a worker can check every item unaided（fixture 資料庫與臨時累積庫）

**Validation method:** session-audit 與 retro-store 的 CLI 測試，用 fixture 資料庫涵蓋下列情況。

**Evidence required:**
- 測試輸出顯示查詢回傳的計數、指針與覆蓋量。
- 2 場不提、3 場提出。
- 拒絕後重新計算。
- 摩擦檔跳過已有改動的規則。
- 現有 session-audit 與 retro-store 測試全過。

**Tests:** `required`

**Test seam:** session-audit 新查詢指令與寫入摩擦檔的輸出；retro-store `list --summary` 的待決定清單。

**Regression scope:**
- 沒有對應改動的規則，寫入摩擦檔的行為不變。
- 摩擦檔已存在的條目不搬動。
- session-audit 的標記與零使用報告不變。
- 現有測試全過。

- [ ] 查詢輸入段落位置與原文，在 session-audit 內部對應自己的規則身分；累積庫不存 session-audit 的雜湊 id — Source: Requirement: 常駐規則被違反時（只讀查詢）
- [ ] 同一標題下其他規則的違反不算進這一段的計數 — Source: Requirement: 常駐規則被違反時（只讀查詢） — Failure: F2
- [ ] 以每筆違反的事件時間過濾：拒絕之前開始、拒絕之後才違反的長 session 要算進去 — Source: Story 13 — Failure: F1
- [ ] 回傳不同 session 數、每筆來源指針，以及覆蓋量（起始時間後分析了幾場、最近分析到何時）；覆蓋量為 0 時明確顯示「尚未分析」，不顯示「零違反」 — Source: Requirement: 常駐規則被違反時（只讀查詢） — Failure: F4
- [ ] 被改段落的原文之後又改過、查不到對應規則時，回報「找不到對應規則」，不回 0 次違反 — Source: Requirement: 常駐規則被違反時（只讀查詢） — Failure: F2
- [ ] 改動生效後剛好 3 場違反時提出「要不要做成 hook」，2 場時不提；系統不自動建立 hook — Source: Story 9 — Failure: F6
- [ ] 提議附至少 3 筆原文指針 — Source: Requirement: 常駐規則被違反時（附原文指針）
- [ ] 使用者拒絕後，以拒絕時間作為新起始時間重新計算，之後又滿 3 場才再提 — Source: Story 13
- [ ] session-audit 寫入摩擦檔時，跳過累積庫裡已有改動錨點的規則；其他規則照常寫入；同一條規則不會同時出現在摩擦檔與待決定清單 — Source: Requirement: 常駐規則被違反時（管道分工） — Failure: F5
