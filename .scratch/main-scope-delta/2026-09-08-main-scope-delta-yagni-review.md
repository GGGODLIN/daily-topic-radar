# Main scope 差額治理 — YAGNI review

## 範圍與席位驗收

本輪只審 spec 是否值得做，不驗證 prompt 效果、不改正式規則、不開始 trial、不建立 tickets、不 commit。

輸入是審前 spec 全文與成對批准脈絡的 neutral packet。主 session 用 Python assert 核對 packet 含 byte-exact spec；逐條分母從 packet 事先定義的26項取得，而非從回覆反推。

逐條表核對輸出：

```text
agy: expected=26 rows=26 missing=0 duplicate=0
c-stories: expected=13 rows=13 missing=0 duplicate=0
c-decisions: expected=13 rows=13 missing=0 duplicate=0
```

agy及c-stories的零刪減報告都附有模型推導條目的刪除損失論述；c-decisions提出N2降為證據註記、N3刪除。三份 shape 驗收通過。c兩批的開工sentinel均存在；vendor路由已告知可能同源，不宣稱模型獨立性。

報告的逐條理由是設計推論，不是已驗證的行為因果。agy的「刪除等同授權」「會自然」「徹底封堵」「全數核准試行」不採為事實、效果保證或使用者授權。原始案例的初版是review否決，不能改寫成使用者先否決、main再換架構規避。引用位置按條目ID與原文核對，不採 reviewer 的不準確packet行號。

## 分母與最簡版

定向案例研究未量化整體頻率或每次損失，標「未定價」。保留的上限是既有規則的一段累積範圍差額檢查、implement共同步驟引用、既有驗證與trial紀錄；不增加新hook、review agent、授權狀態系統或telemetry。

兩席在功能層沒有提出刪減，不能把此結果當作治理有效。c-decisions僅刪減文件性內容，agy對同項主張保留；main接受較小版本，沒有以完備性為由拒絕刪減。

## 完整裁決表

以下ID對應審前packet。accept是spec作者對review處理的裁決，不是使用者新增實作授權。

| ID | 條目 | agy | c | 作者裁決 | 處理依據／結果 |
|---|---|---|---|---|---|
| US1 | 小修自決、增加範圍縮回或問人 | keep | keep | accept | 保留已接受行為界線 |
| US2 | 證據不足收窄結論 | keep | keep | accept | 保留使用者原話 |
| US3 | 以trial觀察效果 | keep | keep | accept | 保留使用者明確要求 |
| D1 | 單一規則及implement引用 | keep | keep | accept | 保留既有入口、兩路適用 |
| D2 | 關鍵時點對照批准基準 | keep | keep | accept | 保留累積差額，不宣稱必定遵守 |
| D3 | 目標與方案要求分開 | keep | keep | accept | 保留語意判斷，不以名稱或數量判越權 |
| D4 | 較小方案不得降低驗收 | keep | keep | accept | 保留授權與驗收邊界 |
| D5 | 否決後可縮小或停止 | keep | keep | accept | 保留原則，不採事故被使用者先否決的誤述 |
| D6 | 差額拍板、無答不做新增 | keep | keep | accept | 保留拒絕與未回答邊界 |
| D7 | ticket／測試不能自證授權 | keep | keep | accept | 保留已接受的核心需求 |
| D8 | 不新增逐次表單、承認prompt限制 | keep | keep | accept | 保留低干擾與不保證聲明 |
| D9 | 沿用既有trial規則 | keep | keep | accept | 不新增排程或監控；實際上線才起算 |
| D10 | trial觀察實際行為及不必要請示 | keep | keep | accept | 保留質性觀察項，不將設計論述當效果證據 |
| T1 | main可見回覆與動作seam | keep | keep | accept | 保留自做與派工兩路行為驗收 |
| T2 | 正向局部自決案例 | keep | keep | accept | 保留不必要請示檢查 |
| T3 | 擴範圍邊界案例 | keep | keep | accept | 保留已提出並接受的案例 |
| T4 | 既有契約與skill行為驗證 | keep | keep | accept | 不另建框架；測試與自然trial分開 |
| T5 | 不重演真實遠端mutation | keep | keep | accept | 保留本次驗證操作界線 |
| O1 | 無新增治理系統 | keep | keep | accept | 保留排除範圍 |
| O2 | 不自行設同scope投入上限 | keep | keep | accept | 尚未拍板，維持排除 |
| O3 | 不改既有review種類與語意 | keep | keep | accept | 不擴大本案 |
| O4 | 不做周邊清理／rollout／Beads結案 | keep | keep | accept | c-stories說可縮寫非必要修改；保留具體排除避免改變邊界 |
| O5 | 不推頻率或全面有效 | keep | keep | accept | 保留證據限制 |
| N1 | 歷史案例可支持的範圍 | keep | keep | accept | 保留來源邊界 |
| N2 | 顧問來源及局限 | keep | demote-v2 | accept | 明標非規範證據註記，不作實作／驗收要求；不刪來源 |
| N3 | 不建立ADR流程備註 | keep | kill | accept | 從spec移除；本review記錄既有gate已判不符合難逆轉條件，不新增ADR |

## 修改與未做

spec只修改Further Notes：N2改為非規範證據註記，N3移除。US／D／T／O與歷史證據界線不變。不為文件性修改重跑review。

尚未修改正式skill／rules、建立tickets或開始trial。治理效果未驗證。無待使用者裁決的需求刪減爭議；下一步由使用者選擇其他Tier review或to-tickets。

## 摩擦盤點

沒有工具執行失敗或未完成席位。審查文字有不準確行號與過度確定的假設，已按原文與ID排除其事實含義；不因此新增流程或修hook。
