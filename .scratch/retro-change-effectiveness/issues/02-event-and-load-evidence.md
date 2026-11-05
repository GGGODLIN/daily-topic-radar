# 02 — 事件與「有沒有載入新版」的共用判斷

**What to build:** 給一個事件位置和一個改動，回答四件事：這是哪個事件、什麼時候發生、當時載入的是不是新版改動、判斷依據是什麼。判斷不出來時，回「未查」並附原因。這個判斷放在 session-retro，以指令提供，驗收器、ingest、回放工具都呼叫它，不各寫一份。驗收器用它幫每筆追蹤觀察補上事件時間與載入判斷。

**Blocked by:** None — can start immediately

**Status:** `ready-for-agent`

**Needs:** None — a worker can check every item unaided（用 fixture session 紀錄）

**Validation method:** session-retro 的 CLI 測試，用 fixture session 紀錄涵蓋下列情況。

**Evidence required:** 測試輸出列出每個 fixture 的事件鍵、事件時間、載入判斷與原因；現有 session-retro 測試全過。

**Tests:** `required`

**Test seam:** session-retro 的指令輸出（新判斷指令，以及 `validate` 對追蹤觀察的補欄）。

**Regression scope:** findings／patterns 的證據正規化與事件時間產生方式不變；現有 session-retro 測試全過。

- [ ] 事件鍵：有工具 call ID 時用它，沒有時用 session UUID＋動作所在的原始行號；證據引文另外存，不拿引文行號當事件鍵 — Source: Requirement: 再犯紀錄（事件與觀察分開定義）
- [ ] 同一個動作同時對應兩個改動時，事件鍵相同，產生兩個觀察鍵 — Source: Requirement: 再犯紀錄（事件與觀察分開定義）
- [ ] 事件時間從原始 session 紀錄取得，模型填的時間值一律不採用 — Source: Requirement: 再犯紀錄（事件時間）
- [ ] 常駐規則：事件之前最後一次 `instructions` 附件含被改段落的新版原文時，判已載入；session 在生效前開始、事件前沒有重新注入新版時，判沒載入 — Source: Story 1 — Failure: F1
- [ ] compact 或換模型後重新注入新版的事件判已載入；同一場在重新注入之前的事件判沒載入 — Source: Requirement: 再犯紀錄（載入判斷） — Failure: F1
- [ ] skill 或 command：以事件之前最後一次 invoke 或重新注入的內容判斷 — Source: Requirement: 再犯紀錄（載入判斷）
- [ ] hook 或 script：事件時間晚於或等於生效時間時判已載入，早一秒判沒載入 — Source: Requirement: 再犯紀錄（載入判斷） — Failure: F6
- [ ] 不符合「生效時間 ≤ 載入時間 ≤ 事件時間」時，不判為已載入 — Source: Requirement: 再犯紀錄（載入判斷）
- [ ] session 檔不存在、缺 `instructions` 附件或有損壞行時，回「未查」並寫出原因，不拋錯、不當成已載入 — Source: Requirement: 再犯紀錄（載入判斷） — Failure: F2
- [ ] 驗收器對追蹤觀察補上事件時間與載入判斷 — Source: Story 1
