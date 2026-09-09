# 01 — 接入 main 範圍差額治理並開始 trial

**What to build:** 在既有派工與驗證工作紀律定義累積範圍差額檢查，由 implement 共同步驟引用，覆蓋 main 自做與 subagent 派工。main 對照使用者最近明確批准的目標與仍有效的邊界，分辨目標要求與選型配套；新增承諾先縮回或請使用者拍板，必要局部修正不增加請示。完成既有行為驗證與受影響契約測試後上線，同時登記既有 trial；不把測試通過或規則載入宣稱為自然使用有效。

**Blocked by:** None — can start immediately.

**Status:** blocked — 修改前 baseline 未重現；需決定驗證前提，尚未修改正式規則

**Needs:** 既有派工與驗證規則、implement skill、skill-creator 行為驗證流程與受影響的契約測試可讀取；具備既有驗證所需的模型執行環境，以及可更新的既有 trial ledger 與 detail。若驗證入口或執行環境不可用，回報具體阻塞，不另建框架或靜默更換模型。沒有額外真實遠端 mutation、跨機部署或生產資料需求。

**Validation method:** 修改 skill 前沿用 skill-creator 的 RED → GREEN → REFACTOR 行為驗證與既有契約測試發現流程。以使用者授權與情境為輸入，觀察 main 可見回覆、派工要求與實際工具動作；main 自做與派工兩路均涵蓋。修改後將 Confirmation／Contract 項目合併驗收，避免逐項重跑相同檢查。合成情境與自然 trial 樣本分開。上線與 trial 登記視為同一交付單元，核對既有 ledger／detail 一致，review 日期依實際上線日與既有規則計算。

**Evidence required:** 修改前行為驗證的實際結果、修改後同契約結果、受影響契約測試輸出、僅包含批准改動的差異、上線版本或內容身分與日期、trial ledger／detail 對應及 review 日期。每個行為案例保留原始授權、main 回覆／派工／工具動作與預期對照；沒有觸發案例或環境不足時不得標為通過。trial 效果留待自然樣本回顧，不要求本票證明有效或產生全量發生率。

**TDD:** waived

**TDD waiver:** non-executable-artifact

**TDD waiver approved:** ticket-breakdown-user-approved

此 waiver 僅指 production-code TDD 不適用；不免除 skill-creator 行為驗證及既有契約測試。使用者已接受單票拆分與這項 waiver；本票落檔不是開始實作的授權。

- [ ] 既有工作紀律為單一定義，implement 共同步驟引用，main 自做與派工皆適用；首次形成實作要求、修改派工／換方案／重派，以及接受會增加範圍的 review 建議前，對照最近批准的目標與仍有效約束，不只比較上一版 plan。 — Source: Story 1
- [ ] **Confirmation：**在新增跨機能力、累積加碼及 review 增加機制的情境中，main 先採符合原授權與驗收的較小方案，或呈現原授權、新增差額、取捨與替代供使用者決定；新增部分在批准前沒有派工實作或直接 mutation。 — Source: Story 1
- [ ] **Confirmation：**本機必要啟用、局部空資料修正與必要測試，以及不改驗收的等效更簡單替換，不增加不必要請示；不得只按 installer 名稱、行數或檔案數判定越權。 — Source: Story 1
- [ ] **Confirmation：**證據不足時收窄結論並揭露未驗證部分，不以補證據為由自行擴測、改設定或增加實作；採較小方案不得降低已批准驗收。 — Source: Story 2
- [ ] **Confirmation：**使用者未回答或拒絕時不執行新增部分，也不靠改名、換 worker、改 ticket／測試／驗收條件替自己授權；原範圍內不相依工作可以繼續。原方案被否決後可縮小或停止，不自動換更大方案。 — Source: Story 1–2
- [ ] **Confirmation：**受影響的既有契約測試通過；不新增 hook、command、review agent、授權收據或機械狀態系統，不改既有 review 種類與授權語意，不自行設定同 scope 投入上限，不做無關清理或跨機 rollout。 — Source: Story 1；已接受的 Implementation Decisions 與 Out of Scope
- [ ] **Contract：**保留行為驗證結果與實際上線日期，供 trial 引用；合成測試不計入自然 trial 樣本，不以靜態文字命中、規則載入或自報遵守宣稱效果。 — Source: Story 3
- [ ] **Confirmation：**上線時登記既有 trial ledger 與 detail，review 依實際上線日加七天；記錄自然樣本應對照的授權、實際動作、先縮回／先問／先做、使用者糾正及不必要請示。樣本不足依既有流程延一輪，不新建排程、採樣 daemon 或 telemetry。 — Source: Story 3

## Verification Log

### 最新決策與實際狀態

修改前baseline未重現後，使用者選擇「也許直接上線看看行為有沒有改善比較快」。本次改採效果未知的自然trial，不追加短情境、不要求有效RED/GREEN作上線前提；原行為驗收項轉為自然trial觀察問題，不標成已證明。這是本次特例，不改skill-creator通用規則。

已寫入本機規則、implement共同引用及inventory備註；trial登記2026-09-08開始、2026-09-15回顧。V1檢查 rule=1、shared_pointer=1、trial_entry=1、detail_identity=matched。修改後11支契約入口依序exit0；SkillEvaluator整體passed，Tier2另有既有測試assertion helper重複建議，未採用、不擴重構。測試输出為本session背景task b5cxrvhqh。只讀差異review回NO_CORE_FINDINGS；trial ledger檔頭要求的三支契約入口亦exit0。本機變更與trial登記已交付；自然行為效果仍未知。

票的Status更新被ticket-yagni gate以Inline沒有worker登記拒絕；未為滿足記帳重做實作、虛構worker、停用gate或新增手動YAGNI審查。原Status欄因此保留舊值，與已寫入本機的事實不同，交付時明示。未commit／push，未宣稱整段implement closeout完成。

2026-09-08：使用者選 Inline。已建立 approved waiver manifest，run_id採原始 /implement 事件UUID；slash未產生gate pending，不宣稱armed。完成修改前fresh-agent決策baseline，S1–S6未命中預定FAIL條件，為無效RED；且短context決策產物不等於原始長session逐字重播。詳同feature的behavior-baseline-result.md。正式規則／skill未改，GREEN／REFACTOR、trial、commit／push未執行。等待使用者決定是否改為效果未知的自然trial探索，不自行擴測或降低驗收。
