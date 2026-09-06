# 單次解題經驗：YAGNI 審查

## 狀態

YAGNI 審查：已收到外部模型完整表與本 session 的需求批次；本 session 的設計／測試批次因串流停滯失敗，未使用任何未完成輸出。不是完整雙席共識。使用者已選 a：證據不足不列候選、不新增待辦，只揭露影響分析的資料限制。審查處置已收斂，未開始拆票或實作，未 commit。

本輪審的是「值不值得做、是否做太大」，不是重新批准既有需求。週二發現、每日報告承接、原始結果證據、排除已有／否決、使用者拍板均保留。

## 輸入與席位

- [原始固定 packet](file:///Users/linhancheng/.claude/.scratch/review-spec-yagni-packet-dbd6580d-43fb-402a-863a-027bf8b584f5.md)：包含 spec 全文、短答與前一則提案成對保存、來源查核與 rubric。主 session 比對 spec 原文成功：`packet_matches_spec: true`。
- [外部審查原始輸出](file:///private/tmp/claude-501/-Users-linhancheng-Desktop-projects/dbd6580d-43fb-402a-863a-027bf8b584f5/tasks/b8y6dkdj3.output)：exit 0，有最簡版、未定價、逐條表及反方下限。
- [正文查閱 focused review](file:///private/tmp/claude-501/-Users-linhancheng-Desktop-projects/dbd6580d-43fb-402a-863a-027bf8b584f5/tasks/bsrp4k26c.output)：exit 0；D4 從 demote-v2 改為 keep。
- [本 session 需求批次](file:///Users/linhancheng/.claude/.scratch/yagni-c-stories-dbd6580d-43fb-402a-863a-027bf8b584f5.report.md)：S1–S5 全列、最簡版、未定價與唯一推導條的反方下限齊備。屬同族自審，不能把它當獨立跨模型證明。
- 本 session 設計批次：已建立開工 sentinel，但 harness 最終通知 `Agent stalled: no progress for 600s (stream watchdog did not recover)`；沒有 valid final，標 invalid，不延長等待或重跑同契約。600 秒是 harness 失敗通知數字，不是實作效能。

枚舉分母來自固定 packet：S1–S5、D1–D5、T1–T5、O1–O5、N1–N2，共22個條目；外部席應全列，本 session 需求席應列5個，其餘17個屬失敗批次。

## 最簡版與未定價

最簡版：只在 distill prompt 增加一種候選，用有對應工具結果的原始紀錄支持做法，定點查已有內容與相關決策，再交既有報告流程。沒有新 channel、排程、服務、資料庫或待辦狀態；實際輸出驗收保留。

兩份有效回報都標示問題尚未定價：沒有可信的遺失經驗頻率、每次損失或新增角度收益。前輪候選1對6不是效益證明。原需求席把它進一步描述成「膨脹失敗模式」的因果理由，主 session 不採用；未證實價值不等於已證明是假候選。

## 逐條結果與作者處置

| # | 條目 | 外部席 | 本 session 席 | 作者處置／說明 |
|---|---|---|---|---|
| S1 | 既有機制加角度 | keep | keep | accept，保留 |
| S2 | 報告交使用者決定 | keep | keep | accept，保留 |
| S3 | 頻率與實際輸出驗收 | keep | keep | accept，保留已確認內容 |
| S4 | 不自行生成技能 | keep | keep | accept，保留 |
| S5 | 無候選／證據不足呈現 | demote-v2 | keep | 使用者裁決 a；不列待確認線索，只揭露分析限制，已落規格 |
| D1 | 修改既有 prompt 與最近測試 | keep | invalid | accept，保留 |
| D2 | 單次經驗不套跨日門檻 | keep | invalid | accept，不更動原分析門檻 |
| D3 | 固定五段式與待確認 | demote-v2 | invalid | accept 格式簡化，已改為簡短做法／證據／落點，必要限制融入敘述；證據不足依使用者a決定不列候選，已同步修改驗收 |
| D4 | 定點讀正文與決策 | 初次demote-v2，複核keep | invalid | 題目誤讀已澄清並複核；accept keep，不全量讀技能庫 |
| D5 | 普通報告／禁止自動修改 | keep | invalid | accept，保留 |
| T1 | 既有報告輸出層驗收 | keep | invalid | accept，保留 |
| T2 | 正例／已有否決／只有自述案例 | keep | invalid | accept，使用者已確認 |
| T3 | 隔離測資、不污染正式待辦 | keep | invalid | accept，保留 |
| T4 | 新增固定措辭契約測試 | kill | invalid | accept，不新增這類字串測試；保留shell語法與既有報告契約，不取代行為驗收 |
| T5 | 最近測試回歸、不全機擴跑 | keep | invalid | accept，保留 |
| O1 | 不安裝上游與自動生成 | keep | invalid | accept，保留 |
| O2 | 不新增channel／服務／狀態 | keep | invalid | accept，保留 |
| O3 | 不自動實作／重啟否決／改頻率 | keep | invalid | accept，保留 |
| O4 | 不宣稱泛化與候選數效益 | keep | invalid | accept，保留；不採用「候選數毫無收益意義」的過強說法，只是不足以證明 |
| O5 | 不順手改造其他系統 | keep | invalid | accept，保留 |
| N1 | 不批准未來候選內容修改 | keep | invalid | accept，保留 |
| N2 | 不新增ADR | keep | invalid | accept，保留 |

## 已落規格的修改

1. D4 明文「先用簡介定位，只定點讀與該候選最接近的正文及決策，不全量讀取技能庫」。原需求即定點讀取，屬說明澄清、不縮減去重驗收。
2. D3 不強制五段式填空；保留值得留下的做法、原始結果證據與落點；影響判斷的條件仍以簡短敘述交代。
3. T4 刪除新增的固定措辭字串測試要求；shell語法、既有報告契約和已批准的實際輸出驗收仍在。這不採用「行為測試必然抓到所有prompt改壞」的說法。

## 已拍板：證據不足的線索（保留原選項）

外部審查的理由原文：
> 「砍掉『標待確認』無具體損失，最簡版無證據直接靜默丟棄即可。」

本 session 需求席主張保留待確認，以區分資料不足與沒有候選。主 session 不採用其把候選數量當膨脹因果證據的部分，但呈現取捨仍是真問題。

- a（建議）：不列成候選、不新增待辦；本次沒有合格候選時明說沒有。若讀取失敗或資料缺失影響分析，只簡短揭露限制，避免把沒分析到說成確認沒有。
- b：報告另附簡短「待確認線索」，同樣不進正式待辦、不自動改內容，讓使用者決定是否值得補查。

推薦a的依據是已批准的目標為有結果支持的解題經驗；不是成本量測或宣稱b無價值。a/b都維持證據門檻，只差未驗證線索是否額外呈現。

使用者回覆「a」，指向上方a選項：不列證據不足候選、不新增待辦，資料缺失影響分析時只簡短說明。S5、D3及T2驗收已同步修改，零未決取捨。下一步由使用者選擇進to-tickets或另做Tier審查；本輪不自動開始實作。
