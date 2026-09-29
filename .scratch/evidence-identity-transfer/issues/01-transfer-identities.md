# 01 — 由原始樣本還原身分

**What to build:** sampler 不再傳遞完整 members 身分副本；判官每筆回樣本序號、判定與引用，確定性驗證層補回原始身分後產生相同報告。

**Blocked by:** None — can start immediately

**Status:** done

**Needs:** None — 本機 Node 測試與隔離 fixture 即可驗收，不呼叫模型或重跑真實 channel。

**Validation method:** sampler／finalizer 整合測試先 RED 後 GREEN，另驗 workflow schema/prompt 與 helper 契約一致，最後跑受影響套件的完整測試。

**Evidence required:** 合法 compact 封包產出原身分報告；錯誤封包不產出 report/receipt；歷史格式測試不回歸；保留 RED 與 GREEN 的指令及結果。

**TDD:** required

**TDD seam:** sampler CLI 輸出與 finalizer 是否發布正確報告及收據

- [x] sampler 輸出無完整 members 身分副本，合法序號的主審／複核結果按原 manifest 還原時間、session、path。— Source: Story 1
- [x] 缺漏、重複、錯序、越界、混合新舊格式的判定不得發布。— Source: Story 2 — Failure: F2/F6
- [x] nonce/hash 不符、來源篡改、Read 收據不完整或跨 transcript、最新壞封包仍拒絕，不能用較舊成功結果遮蔽。— Source: Story 2 — Failure: F1/F4
- [x] workflow 與 helper 採同一 compact 契約；PASS 複核保持原流程。— Source: Story 2 — Failure: F5
- [x] 舊報告與舊身分封包保持原驗證；不改 rubric、抽樣量、模型或真實歷史資料。— Source: Story 3

## Verification Log

- 執行模式：使用者明示「你直接做就好，不用這麼麻煩」；main 直接實作，不再派實作 worker或追加方案審查。
- feature_base_sha：social-info `cbc0ff6b3b4d888dda1ea15e5090c0f126d37b9b`；全域 workflow repo `a4618486f6ae24b6e2c913bb8cc92aff15d30c91`。
- 僅一個完整垂直修補；既有其他 session 的修改不動。失敗資料留作證據，不放寬驗證過關。
- RED：`node --test --test-name-pattern='^compact' scripts/local-analysis/evidence-level-sample.test.mjs`，4項中3項因尚未支援compact失敗、1項既有拒絕行為通過；實作後同4項全過。
- GREEN／Confirmation：helper與新workflow契約測試73/73；repo pytest 207/207；既有report contract PASS；shell語法與git diff --check通過。主審採原標記序號，PASS複核採其子集合從1開始，錯誤及混合row格式拒絕。
- 相容性：舊格式的身分比對仍在；compact列只依原manifest還原三個身分欄位。沒有修改歷史manifest/report或rubric。
- 實際smoke：新程序載入evidence-level-auditor，只用Read讀隔離fixture，實際Read收據含隱藏測試字串；回傳sample_index=7、result=FAIL及精確引用，不含timestamp/session/path。標準Bash寫檔probe不適用只讀agent，改驗其真實讀取任務；未重跑完整live workflow。
- 既有失敗：全域舊routing suite在feature base與本次均67/74，相同7條report-handoff過期斷言。只同步本次改動的輸入fixture，不擴修無關斷言；不宣稱該套件全綠。
- Review：`/code-review low`工具兩次結束皆無可用文字／findings收據，不計獨立review PASS。main另讀完整production diff核對序號綁定、明確格式辨識、既有nonce/hash/Read檢查與caller接線，未發現需再修的本次功能缺陷。
- Gate原始拒絕：`[ticket-yagni] 票 01 沒有登記 worker，YAGNI 未審，不能標完成。` 本輪是使用者指定main直接改，沒有假造worker或停用gate；Status維持原值。程式與測試已落地，不因票面未閉合重做實作。
- 先前發布限制：共用workflow被provenance標共寫。使用者要求收尾後，僅將本session可逐行對回的差異套入各自最新origin/main的乾淨worktree；六個repo檔與三個全域檔共9/9逐位元組一致，原共用工作區未清除或覆寫。
- 補審：既有ticket-yagni手動入口已對兩repo實際執行。repo裁決148 keep／1 kill；kill指spec文件，不採納，因它是使用者已批准的設計與本票Story來源，刪掉會破壞驗收追溯。全域裁決17 keep／183 demote；demote均為檔內既有未改符號，不是要求刪掉既有功能，故不列為本票交付、不修改。
- 發布版本驗證：乾淨worktree的相關Node測試73/73、pytest207/207，diff --check通過；未新增code修法、不重跑無關channel。TDD decision manifest仍保留，validate valid=true。
- 關票提示處理：gate已送達唯一spec刪除建議；依使用者已批准的spec與本票Story來源核對後保留，不改code，沿用同一內容的成功測試。依既有gate的下一次狀態更新路徑關票，未改任何gate設定或verdict。
