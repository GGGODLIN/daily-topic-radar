# 09 — 三份文件同步，加上第三、四層的操作流程

**What to build:** retro-workflow 的指令說明、流程腳本、累積契約三份文件一起更新，讓執行者照文件做出來的流程跟新程式一致。內容包括：
- 逐筆觀察與四種去向。
- ingest 自動匯入觀察。
- 事件時間與載入判斷由程式產生。
- 判定流程、待決定清單、提議決策。
- 第三層整場測試的做法，以及做不到時的副作用替代檢查。
- 第四層對照過去紀錄。
- 程式類改動要把當初出錯的原始輸入（去掉私人資訊後）加進工具自己的測試。

**Blocked by:**
- 04 — 匯入時自動寫入觀察
- 05 — 判定、提議決策、待決定清單
- 06 — 規則違反查詢與管道分工
- 07 — 重犯判斷
- 08 — 回放工具（若 01 判可行而執行了 08）

**Status:** `done`

**Needs:** None — a worker can check every item unaided

**Validation method:** retro-workflow 契約測試。測試要檢查三份文件之間的交接一致，例如累積契約寫的匯入方式和指令說明相同，不只檢查關鍵字有沒有出現。

**Evidence required:**
- 契約測試輸出顯示三份文件的交接斷言通過。
- 舊說法的反例斷言通過，例如累積契約已不再要求人工逐筆 observe。
- 既有契約斷言全過。

**Tests:** `required`

**Test seam:** retro-workflow 契約測試對三份文件內容的斷言。

**Regression scope:** 與本次改動無關的既有規定不變，例如起跑確認、模型選擇、驗收階段順序；既有契約斷言全過。

- [x] 三份文件對逐筆觀察、四種去向、ingest 自動匯入、事件時間與載入由程式產生的說法一致 — Source: Requirement: 流程說明 — Failure: F5
- [x] 累積契約不再要求人工逐筆 observe，也不再寫「不自動輸出 effective」 — Source: Requirement: 流程說明
- [x] 指令說明寫出判定流程：每輪累積後列出待驗證改動與成本估算，由使用者選要跑哪些；判定結果與待決定清單在哪裡看 — Source: Requirement: 判定與撤回（判定流程）
- [x] 第三層流程寫明：同一個模型；題目不能寫出失敗標籤或預期答案；副作用以不適用的任務做新舊對照；做不到時判定標「副作用未檢查」 — Source: Requirement: 分層驗證（第三層）
- [x] 第四層流程寫明逐一列出過去事件，標出擋得住與擋不住的 — Source: Story 7
- [x] 程式類改動的流程寫明：當初出錯的原始輸入去掉私人資訊後，加進工具自己的測試 — Source: Story 4
- [x] 01 判不可行而刪除 08 時，文件寫明文字規則類從整場測試開始 — Source: Requirement: 分層驗證（第一張工作單）

## Verification Log

- 2026-11-06 完成（~/.claude）：文件同步的主體被另一個 session 的收尾 commit `e10a8fc0`（chore(closeout): checkpoint shared ledgers）一起收進去，因為當時已 `git add` 等待那個 session 的 memory 檔離開 index；內容正確，訊息不是本票寫的，未改寫歷史。後續補充在 `e97b26a2`。紅燈基線：契約測試 19 個中 12 敗、流程腳本測試 12 個中 1 敗，原因是斷言找不到新文字。綠燈：retro-workflow-contract `18/18`、retro-workflow.test `11/11`。變異：把累積契約改回「用 observe 保存」兩個測試失敗；把模型不可用改成第三層，交接測試失敗。
- 驗收第 7 條改寫：01 判可行、08 已完成，所以文件寫「文字規則類走回放工具；回放回報無法回放時改走第三層、模型不可用時改走第四層」，並有斷言確認沒有「文字規則類從整場測試開始」。
- 指令說明：步驟 0 改成 ingest 自動寫入、observe 只留人工補單筆，新增「看判定結果與待決定清單」；步驟 2 的 tracking-context 改由 `retro-store.py tracking-context` 產生；步驟 3 補 role 與 covered、逐筆去向；步驟 5 補 ingest 結果分類；新增步驟 6（判定流程、四層、判定規則、hook 提議的管道分工）。累積契約：補 `validated/cross-session.json` 唯一匯入來源、逐筆去向、更正紀錄、舊格式另列，新增「判定與待決定清單」。流程腳本：reading 要求再犯填 `covered`，後段照原值保留。
- skill-creator：worker 已 invoke 並做靜態核對（文件引用的指令與旗標都在 `--help`），回報需要新鮮 agent 的行為驗證（NEEDS_DELEGATION）。main 派一個只讀 agent 照文件回答 4 個情境（累積後列估算等使用者選、cannot_replay／model_unavailable 換層、第三層出題限制與副作用未檢查、程式類回歸輸入），4 個都答對。它指出 3 個會讓人做錯的缺口，main 補進 `e97b26a2`：前置檢查回報 `unfaithful` 時換事件、沒有可用事件改走第三層；第一層回歸案例要先在改動前確認會失敗；加回歸測試屬於驗證，不算改正式程式。其餘它標為要猜的地方（第一、三、四層的成本怎麼估、第三層用哪個 harness、副作用對照每邊幾場）留作 Minor。
- ticket-yagni：kill 0／demote 1／keep 7。刪掉 `workflows/tests/retro-workflow.test.mjs` 新增的逐階段 covered 檢查（契約斷言已涵蓋），該檔回到 25aef02b 的內容。
