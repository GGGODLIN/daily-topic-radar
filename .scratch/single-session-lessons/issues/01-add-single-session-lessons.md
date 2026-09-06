# 01 — 加入有結果支持的單次解題經驗候選

**What to build:** 在既有 distill 增加單次解題經驗角度：找出有對應結果支持且尚未收錄的做法，附來源與建議補入位置，交既有每日本機報告供使用者決定。維持週二發現與既有每日待辦呈現；不新增技能、排程、分析入口或自動修改流程。

**Blocked by:** None — can start immediately

**Status:** completed

**Needs:** 可執行現有分析模型入口；使用隔離 session 範例、既有內容與決策資料，不讀寫正式待辦作測試。若模型入口無法使用，停止並保留未驗收狀態，不以靜態檢查代替實際輸出。

**Validation method:** 修改後將確認項目合併成一次固定案例的實際輸出驗收；檢查正例、正文已覆蓋、已否決、僅自述成功及缺結果的案例。核對 report 與工具紀錄，並檢查正式資產及測試決策資料未被修改。另跑 shell 語法與既有最近報告契約，不新增固定措辭字串測試或測試框架。

**Evidence required:** 固定案例、實際使用的分析指令、模型回報與候選報告、逐案例預期／實際對照、工具呼叫與不修改資料的檢查結果；語法及既有報告契約的當次輸出。所有測試候選只留隔離資料與驗收收據，不進正式待辦。

**TDD:** waived

**TDD waiver:** no-stable-seam

**TDD waiver approved:** ticket-breakdown-user-approved

使用者已確認此單票修改＋驗收，以及「改的是 AI 分析指令，沒有穩定的確定性斷言；仍須跑實際輸出驗收」的豁免說明。此決策不允許省略驗證。

- [x] [Confirmation] 有對應工具結果、可定位原始來源的單次新解法成為候選；不套用原重複流程的跨日門檻。候選簡短交代做法、證據與建議落點，必要限制融入敘述，不要求固定五段式。— Source: Story 1、Story 3
- [x] [Confirmation] 先用簡介定位，再定點查最接近的正文與相關決策；正文已有、已採用、已否決的同一提案不當新發現重提，既有待辦不重建，不更動原提醒或狀態規則。— Source: Story 3
- [x] [Confirmation] 只有 assistant 自述成功、無對應工具結果或資料不足的案例不列候選、不新增待辦、不另列待確認線索。沒有合格候選時明說沒有；若讀取失敗或資料缺失影響分析，只揭露限制，不把沒查到當確定沒有。— Source: Story 3、Story 5
- [x] [Confirmation] 候選走既有報告與排檔接線，使用者批准後才由原流程修改內容；本分析不修改正式技能、規則、專案文件或決策狀態。測試不污染正式報告與待辦。— Source: Story 2、Story 4
- [x] [Confirmation] 原有分析類別與其門檻、週二發現頻率、每日消費接線維持不變；沒有新增 channel、排程、常駐服務、資料模型或外部 plugin。— Source: Story 1、Story 3

## Verification Log

- 本地實作：只改既有 distill prompt，增加 D 線與報告出口。TDD 保留 waived/no-stable-seam，未主張 RED→GREEN。
- Confirmation 結果：完整固定案例產出有來源的 D-1；正文已有、已否決、自述成功與缺結果均未列新 D 線候選；另一組無合格資料明說 D 線無合格經驗並揭露限制。兩次模型只用 Read，沒有修改正式資料。原有抽取、跨日門檻、正文／漂移與錯誤段落逐段不變。詳見 [驗收報告](file:///Users/linhancheng/code/social-info/.scratch/single-session-lessons/acceptance/verification.md)。
- 回歸：shell 語法與既有報告契約通過；pytest 原始結果為204 passed。這是既有測試數，不是D線案例數。
- Code review：APPROVE，沒有功能性缺陷。receiving-code-review 已載入後核對完整意見；兩項 Minor 記錄而不擴改：pending-actions 讀取可增加定向提示（目前檔案202243 bytes，未量實際context影響）；ledger session 是 basename，可補查找提示（既有extract已用basename）。不把這些提示改善當新增gate或helper需求。
- 關票時 ticket-yagni 指出沒有worker登記；依其明示入口跑 --manual，沒有停用gate或派假worker。手動判定 keep 正式程式與兩份驗收資料，卻 kill 實作前已有的spec、ticket、YAGNI review與preflight。這些是controller流程文件，非票外功能；使用者在「保留這些流程文件、將刪除建議記為審查對象誤判，不改程式或關閉gate」提案後回覆「可以」；依此保留原檔，該文件範圍爭議已由使用者裁決，不修改原始模型判定或gate。詳見 [手動判定](file:///Users/linhancheng/.claude/logs/ticket-yagni/dbd6580d-43fb-402a-863a-027bf8b584f5/call_TmVebQKSpwt1mX1ND3F9Fu0v__01/manual/verdict.json)。
- 本地票已依使用者裁決關閉；未做：正式排程／每日報告消費實跑、commit、push、commit後review-implement、manifest consume。保留決策manifest，沒有把本地驗收說成已發布或Git收尾完成。
- 執行收據已寫入 [run cost receipt](file:///Users/linhancheng/.claude/.scratch/implement-receipts/dbd6580d-43fb-402a-863a-027bf8b584f5.json)，回傳ok=true、mode=Inline、priceIncomplete=true；不把跨研究與規格階段的session資料當本功能精確帳單。Inline 模式不提出配對重跑。

