# 02 — 事件與「有沒有載入新版」的共用判斷

**What to build:** 給一個事件位置和一個改動，回答四件事：這是哪個事件、什麼時候發生、當時載入的是不是新版改動、判斷依據是什麼。判斷不出來時，回「未查」並附原因。這個判斷放在 session-retro，以指令提供，驗收器、ingest、回放工具都呼叫它，不各寫一份。驗收器用它幫每筆追蹤觀察補上事件時間與載入判斷。

**Blocked by:** None — can start immediately

**Status:** `done`

**Needs:** None — a worker can check every item unaided（用 fixture session 紀錄）

**Validation method:** session-retro 的 CLI 測試，用 fixture session 紀錄涵蓋下列情況。

**Evidence required:** 測試輸出列出每個 fixture 的事件鍵、事件時間、載入判斷與原因；現有 session-retro 測試全過。

**Tests:** `required`

**Test seam:** session-retro 的指令輸出（新判斷指令，以及 `validate` 對追蹤觀察的補欄）。

**Regression scope:** findings／patterns 的證據正規化與事件時間產生方式不變；現有 session-retro 測試全過。

- [x] 事件鍵：有工具 call ID 時用它，沒有時用 session UUID＋動作所在的原始行號；證據引文另外存，不拿引文行號當事件鍵 — Source: Requirement: 再犯紀錄（事件與觀察分開定義）
- [x] 同一個動作同時對應兩個改動時，事件鍵相同，產生兩個觀察鍵 — Source: Requirement: 再犯紀錄（事件與觀察分開定義）
- [x] 事件時間從原始 session 紀錄取得，模型填的時間值一律不採用 — Source: Requirement: 再犯紀錄（事件時間）
- [x] 常駐規則：事件之前最後一次 `instructions` 附件含被改段落的新版原文時，判已載入；session 在生效前開始、事件前沒有重新注入新版時，判沒載入 — Source: Story 1 — Failure: F1
- [x] compact 或換模型後重新注入新版的事件判已載入；同一場在重新注入之前的事件判沒載入 — Source: Requirement: 再犯紀錄（載入判斷） — Failure: F1
- [x] skill 或 command：以事件之前最後一次 invoke 或重新注入的內容判斷 — Source: Requirement: 再犯紀錄（載入判斷）
- [x] hook 或 script：事件時間晚於或等於生效時間時判已載入，早一秒判沒載入 — Source: Requirement: 再犯紀錄（載入判斷） — Failure: F6
- [x] 不符合「生效時間 ≤ 載入時間 ≤ 事件時間」時，不判為已載入 — Source: Requirement: 再犯紀錄（載入判斷）
- [x] session 檔不存在、缺 `instructions` 附件或有損壞行時，回「未查」並寫出原因，不拋錯、不當成已載入 — Source: Requirement: 再犯紀錄（載入判斷） — Failure: F2
- [x] 驗收器對追蹤觀察補上事件時間與載入判斷 — Source: Story 1

## Verification Log

- 2026-11-06 完成（commit c487263）：worker 先寫 10 個測試，紅燈基線 failures=8、errors=1，原因是 `invalid choice: 'event-evidence'` 與 `KeyError: 'events'`；實作後 `Ran 38 tests ... OK`。worker 另做 7 種變異，全部被測試抓到。ruff 的 2 個錯誤（F401 `math`、E731 `near`）在基準 cbcbc3f 就存在，不是本票新增。
- 指令介面：`session-retro.py event-evidence --session-file <jsonl> (--source-line N | --tool-call-id ID) --change '<JSON>'`；change 欄位 `id`、`kind`（rule/skill/command/hook/script）、`effective_time`、`new_text`、`name`；輸出 `event_key`、`event_key_kind`、`observation_key`、`event_time`、`loaded`（loaded/not_loaded/unchecked）、`basis`、`reason`、`load_line`、`load_time`。也可 import `event_evidence()`。
- Minor：`validate` 從 run 目錄的 `tracking-context.json` 找改動定義（`id`／`change_id`、`kind`、`effective_time`、`new_text`、`name`）。目前流程沒寫這些欄位，找不到時載入判斷回「未查」。09 文件同步要讓 retro-workflow 寫 tracking-context 時帶這幾欄，03／04 接線時也要注意。
- Minor：skill／command 在事件前完全沒有 invoke 時判 `not_loaded`（`never_loaded`），不是未查。command 內容取「invoke 那行加緊接的 isMeta 行」，只有合成資料測過。
- Minor：同一行有多個 tool call 又沒給 `--tool-call-id` 時，事件鍵退回 `session:行號`。呼叫端能給 ID 就給，否則同一動作可能得到兩種鍵。
- Minor：損壞行只在事件行本身或事件之前時判未查；hook／script 只看事件行。這是對「有損壞行回未查」的收窄，有測試鎖住。
- 2026-11-06 ticket-yagni：kill 1／demote 0／keep 18。砍掉 `squash`（新版原文比對前的空白摺疊），改回逐字比對（commit ab98f0a）；`Ran 38 tests ... OK`。後果：新版原文只要有一個空白或換行不同，就判 `last_load_is_old_version`。
- 2026-11-06 證據角色（F6，使用者「照建議」，spec「事件與觀察分開定義」）：social-info commit bd2e7fd、~/.claude commit 25aef02b。追蹤觀察的 evidence 每筆帶 `role: action|context`；只有 action 產生事件；context 歸到同一 session 中行號不晚於它的最近 action（沒有就歸第一個 action，該 session 沒有 action 則驗收不通過），存成補充證據；非 unreviewed 的觀察至少一筆 action；整筆沒有 role 的舊報告寫成舊格式觀察，不算再犯。紅燈：session-retro 新 6 個、retro-store 新 3 個、retro-workflow 新 2 個，原因功能缺失；retro-import 新 2 個先跑就綠（本來就原樣傳遞）。綠燈 session-retro `Ran 64 tests ... OK`、retro-store 62、retro-import 16、retro-judge 20、retro-replay 26、session-audit-rule-query 17、retro-workflow.test.mjs 11/11、retro-workflow-contract 6/6。仿「搜尋輸出過大」的合成 fixture：6 筆證據（2 action、4 context）只產生 2 個事件與 4 筆補證。
  - 既有 fixture：session-retro 23 處追蹤證據補 `role: "action"`；retro-store `tracked()` 的 action 證據補 role；retro-import fixture 補 role。原因是契約改了，斷言沒有放寬。
  - ticket-yagni（這次修正）：kill 1／demote 5，全部不採用。審查員以本票原驗收為準，沒有看到使用者 2026-11-06 對證據角色與舊格式的決定（spec「事件與觀察分開定義」最後一條）；被點名的 `legacy_events`、角色檢查、context 落庫與對應測試，正是在實作該決定。
  - Minor：cross 合併多份報告時 context 依合併後的 action 重算歸屬，可能掛到跟原單場報告不同的 action，沒有真實合併資料驗證。模型把補救動作誤標成 action 時驗收器抓不到，只能靠提示文字約束。11-03、11-04 既有 validated 報告沒有 role，重跑 reading 驗收會全被擋，只能走舊格式匯入。
- 2026-11-06 收尾審查（python-reviewer／typescript-reviewer／security-reviewer，pi-subtask 跨模型 gpt-6.1-sol(high)，8 段）：以下判定成立，main 抽查程式碼確認；套用 final-gate-materiality 後沒有一項可判「成立但不重要」（安全項屬 G4，其餘會寫出錯誤判定或資料且事後看不出，不適用 M2；測試缺口經審查員變異證明測試仍綠，不適用 M4）。處置：修。
  - [HIGH] session-retro.py:1554 command 載入收集 invoke 後所有 isMeta 文字，混入其他指令的新版內容 → 判已載入（Acceptance-miss）
  - [HIGH] session-retro.py:1715 沒有 action 的觀察保留模型手填 events，cross 逐筆核對假通過（Acceptance-miss）
