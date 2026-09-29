# Evidence-level 身分資料傳遞修補

## Problem Statement

本機分析原始 manifest 的內容與 checksum 正確，但 sampler agent 回傳批次資料時抄錯成員 path；workflow 又把這份轉錄當作預期身分，造成正確輸出遭拒。恢復過程曾沿用錯誤轉錄，進一步製造錯誤提示。判官本身也可能在回傳時間、session 與 path 時抄錯。

實作前只透過原始樣本及既有 finalizer 恢復當日報告，正式流程仍有相同轉錄面。修補目標是減少這個錯誤來源，不重寫整套分析，也不把格式恢復當成判官語意一定正確。

## Solution

讓 AI 每筆只回「樣本序號、判定、引用原句」。程式根據當次原始 manifest 還原時間、session 與 path，不再要求 AI 重抄這些身分欄位。sampler 的傳輸封包也不再攜帶需要 AI 複製的完整 members 身分表。

nonce、hash、完整 Read 收據、引用原句、批次覆蓋與歷史報告相容性仍由既有確定性驗證層負責；任何無法綁定原始樣本的輸出仍然拒絕發布。

## User Stories

1. 作為本機分析使用者，我希望 AI 只回樣本序號與判定，讓原本正確的檔案路徑不再因重抄而變形。[user: "都可以"]
2. 作為報告消費者，我希望程式只接受完整且能對回原始樣本的結果，不把格式修補變成放寬驗證。[evidence: 2026-09-27 原 manifest、錯誤 sampler 回傳與原 finalizer 驗收紀錄]
3. 作為既有報告的使用者，我希望本次修補不改寫歷史 manifest、報告或收據，也不要求重跑成功 channel。[evidence: 本輪已確認的最小修補範圍與歷史相容性驗收條件]

## Implementation Decisions

- 只調整 evidence-level 的 sampler 傳輸形狀、判官輸出形狀及對應確定性還原；不新增 runner、常駐服務、排程或維護帳本。[user: "都可以"]
- 原 manifest 保持唯一身分正本；workflow 不再把 agent 回傳的完整 members 副本當作身分驗證依據。派工仍使用已知批次及來源綁定資料。[evidence: 本輪 sampler 回傳的成員 path 與原始 manifest 不同，且原始 checksum 未變]
- 每筆判定保留序號、PASS／FAIL 與違規類型及引用；移除需 AI 重抄的時間、session 與 path。頂層 nonce、批次和 hash 綁定不因精簡每筆欄位而取消。[inferred]
- 序號必須對應當次樣本檔的顯示序號，並在該 nonce 與批次綁定的集合內解析。主審批次與 PASS 複核不可混用對照；序號不得越界、重複、缺漏或錯序。[inferred]
- 程式在驗證成功後，由原始樣本補回完整身分，交給既有報告生成流程。報告欄位及對外格式維持原狀。[inferred]
- 保留原始樣本及批次 hash、attempt nonce、完整讀取與同一 transcript 的要求，以及引用必須來自對應 answer 的檢查。驗證失敗時不發布報告、不寫成功 checkpoint，也不回退採用同一 attempt 中較舊的成功封包掩蓋最新錯誤。[evidence: 既有 finalizer 與批次驗證測試]
- 新舊封包相容處理必須有明確的格式辨識；不得以「解析失敗就當成另一格式」靜默放行。既有已發布報告與收據維持可驗，不修改它們來配合新程式。[inferred]
- 本次不修改判官 rubric、模型選擇、抽樣數量或語意判定，也不承諾消除所有 AI 誤判或所有控制欄位的轉錄風險。[evidence: 使用者批准的是身分資料傳遞的最小修補]

## Testing Decisions

沿用既有 sampler／finalizer 的整合測試邊界，以「是否發布正確報告、是否寫入成功收據」驗收，不以内部函式名稱或 prompt 措辭當成唯一測試。

1. 合法的精簡判定封包，還原出的時間、session、path 必須逐項等於原始 manifest；sampler 傳輸封包不得再包含完整 members 身分副本。
2. 序號越界、錯序、重複或缺漏時不得發布；主審批次與 PASS 複核各自按綁定集合解析。
3. nonce、attempt、hash 不符，或樣本／Read 收據被改、讀取不完整、跨 transcript 組合時，仍不得發布。
4. 主審含 PASS 的情況必須走原有再複核；混合 PASS／FAIL、全部 FAIL 與缺失批次都保留原語意。
5. 舊格式報告與收據仍可驗證；原有的錯誤身分、錯誤引用與最新壞封包拒絕測試不能刪除或放寬。
6. 先用本次錯誤形狀建立會失敗的回歸測試，再做最小修改；不以真實歷史檔作可寫 fixture，不重跑整套 daily-local。

既有測試入口：[evidence-level-sample.test.mjs](/scripts/local-analysis/evidence-level-sample.test.mjs)。受影響的 workflow 契約測試另按實際修改盤點，不覆蓋其他 session 的未提交變更。

## Out of Scope

- 三項服務的重啟與 keeper 資料庫 migration；它們是本輪另一條已授權操作。
- 新增 hook、新的判官或全面改善證據層級 rubric。
- 重新評分本日既有報告、重抽樣、修改歷史 transcript／manifest／checksum。
- 跳過 Read 收據、取消 nonce/hash 綁定、用舊成功輸出遮蔽新錯誤。
- 更換 Workflow runtime、模型供應商、帳號設定或 ccp。

## Further Notes

使用者確認方案後，再明示「你直接做就好，不用這麼麻煩」；本輪採 main 直接實作，不重開方案選單。compact sampler／主審／PASS 複核與原 manifest 身分還原已在本機落地，相關 Node 測試73項與pytest207項通過。新程序判官的Read＋精簡JSON回傳也已實測；未重跑完整live日報。

先前票面關閉遭worker-only gate拒絕；後續使用既有手動補審入口取得兩repo裁決、核對意見後已關票，未偽造worker或停用gate。發布採最新origin/main的乾淨worktree，僅帶入本session可逐行對回的差異；發布結果另以commit與遠端核對收據為準。舊routing suite在修改前後均有相同7條既有失敗。細節見[Verification Log](/.scratch/evidence-identity-transfer/issues/01-transfer-identities.md)，不要重做已驗證的實作。

ADR 判定：本次是可逆的私有資料契約修補，不符合「難以回復」條件，因此不另立 ADR。

本次操作證據保留在當日的本機分析紀錄，不隨此 spec 發布；實作時應把去識別化的錯誤形狀轉成回歸測資，不依賴私人 session 路徑。
