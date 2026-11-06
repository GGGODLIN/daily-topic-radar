# 07 — 重犯判斷

**What to build:** 拿新舊兩組的結果（回放下一步、整場測試輸出，或第一層測試結果）和改動的重犯條件，產生判定並寫進累積庫。

- 重犯條件能寫成程式判斷式的，用程式判；不能的才交給模型判，而且不讓模型知道哪一組是新規則。
- 同時檢查任務有沒有合理往前推進。
- 套用 01 凍結的門檻與判斷規則。

**Blocked by:**
- 01 — 回放可行性驗證（關卡）
- 05 — 判定、提議決策、待決定清單

**Status:** `done`

**Needs:** None — a worker can check every item unaided（假模型端點與臨時累積庫）

**Validation method:** 判斷指令的 CLI 測試，用假模型端點回傳預先寫好的判斷結果；檢查送給模型的內容，以及寫進累積庫的判定紀錄。

**Evidence required:**
- 測試輸出顯示：程式判斷式的案例沒有呼叫模型；送給模型的內容不含組別標籤；各種組合對應到正確的判定值；模型呼叫失敗時沒有產生判定。

**Tests:** `required`

**Test seam:** 判斷指令的輸出，以及 retro-store 收到的判定紀錄。

**Regression scope:** 05 的判定紀錄格式與待決定清單不變；現有 retro-store 測試全過。

- [x] 重犯條件可寫成程式判斷式時，用程式判斷，不呼叫模型 — Source: Requirement: 分層驗證（判斷有沒有重犯）
- [x] 交給模型判斷時，送出的內容不含哪一組是新規則、哪一組是舊規則的標籤 — Source: Requirement: 分層驗證（判斷有沒有重犯）
- [x] 目標動作沒有出現、但任務也沒有往前推進時，不算有效 — Source: Requirement: 分層驗證（判斷有沒有重犯） — Failure: F2
- [x] 第二、三層：舊規則組重現達凍結門檻、新規則組明顯下降時判有效；兩組差不多判無效；舊規則組重現不到時記為驗證不成立，判證據不足 — Source: Requirement: 判定與撤回（對應規則）
- [x] 第一層測試通過時判有效，證據層級標「只涵蓋已知輸入」 — Source: Story 4
- [x] 第四層只能判無效或證據不足 — Source: Requirement: 判定與撤回（對應規則）
- [x] 不相關時刻的結果顯示新規則多出原本沒有的動作時判幫倒忙，而且幫倒忙優先於有效；沒做副作用檢查時，判定標「副作用未檢查」 — Source: Story 10
- [x] 模型判斷呼叫失敗、逾時或回傳空內容時，不產生判定，並回報失敗原因 — Source: Requirement: 判定與撤回（判定流程） — Failure: F4

## Verification Log

- 2026-11-06 完成（commit 7b19d0f，新檔 `retro-judge.py`、`retro-judge.test.py`）：紅燈基線 15 個測試全紅（stub 只回 not implemented，45 個失敗斷言）；worker 另對 14 處實作做變異，全被測試擋下。綠燈 retro-judge `Ran 20 tests ... OK`、retro-store 50 OK。
- 介面：`retro-judge.py [--store DIR] --input FILE [--apply] [--endpoint URL] [--judge-model M] [--timeout 秒]`；條件含 `predicate:<名稱>@<版本>` 時用程式判（目前 `root-search-without-exclude`、`stale-element-index`，版本 `gate-2026-11-06-v1`）；判定一律透過 `retro-store.py verdict` 寫入；模型失敗 exit 3、不寫判定。第一層判有效時輸出 `evidence_scope: "known_inputs_only"`。
- ticket-yagni：kill 1／demote 10／keep 23；審查員註明缺 01 凍結規則原文。照做 6 項：`Invalid`、`need`、`string`、`whole`、`run_list` 收斂成一個欄位層級檢查；`extra_actions` 門檻改成照規格字面出現 1 次就算。不採用 5 項，理由是 01 報告「凍結參數」第 5 點明寫：`reply_text`、`progressed`（沒推進＝沒有工具呼叫且文字少於 200 字元）、`root_search`、`stale_index`、`predicate_for`（判斷式寫進 condition、帶版本號）。v2 綠燈 retro-judge `Ran 20 tests ... OK`。
- worker 自訂判斷：模型端點用 Anthropic Messages 格式（對真實端點未驗證）；每筆回應各一次模型呼叫，不送 id、組別、改動 id、thinking；第一層測試失敗判無效；第四層全部擋不住判無效、其餘證據不足；多出動作的鍵＝非 Bash 用工具名、Bash 用每段指令第一個詞；幫倒忙蓋過任何主判定。
- Minor：retro-store `verdict` 指令本身仍不擋「第四層判有效／幫倒忙」，規則只在 retro-judge 強制。前置重現檢查（原樣 5 次至少 2 次）不在 retro-judge，由 08 負責。一次判定只對應一個事件，多事件合併規則沒做。hook 的副作用抽樣檢查沒做（本票驗收只要求「新規則多出動作」）。`stale-element-index` 判斷式沒用真實 LCU 輸出驗過。
- 2026-11-06 收尾審查（python-reviewer／typescript-reviewer／security-reviewer，pi-subtask 跨模型 gpt-6.1-sol(high)，8 段）：以下判定成立，main 抽查程式碼確認；套用 final-gate-materiality 後沒有一項可判「成立但不重要」（安全項屬 G4，其餘會寫出錯誤判定或資料且事後看不出，不適用 M2；測試缺口經審查員變異證明測試仍綠，不適用 M4）。處置：修。
  - [HIGH] retro-judge.py:125 root_search 只採第一個相關片段（Acceptance-miss）
  - [HIGH] retro-judge.py:397 第一層 cases=0 判有效（Failure-miss: F6）
  - [MEDIUM] retro-judge.py:405 第四層 events=[] 判無效（Failure-miss: F6）
  - [LOW→G4] retro-judge.py:223 urlopen 跟隨跨來源重新導向，帶 API key 外送（安全，無標記）
