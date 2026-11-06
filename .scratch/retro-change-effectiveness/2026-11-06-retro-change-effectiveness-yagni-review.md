# YAGNI 審查：retro 改動有效性 spec（2026-11-06）

受審：[spec.md](/.scratch/retro-change-effectiveness/spec.md)（commit e1d91d5 版本，審後已依下方處置修改）。

## Run

- 審查軸：YAGNI（值不值得做），使用者在 to-spec 落檔選單選 `f`，席位選 `c`（c 席 Fable＋web GPT Pro 席）。
- 前輪紀錄：查無前輪紀錄（本目錄第一份 review 檔）。
- 審查材料：spec 全文、使用者原話附錄（短答與其選項成對）、研究關鍵數字表、審查標準，共 10,680 字元；不含作者評估。
- 席位與實際模型：
  - c 席 stories：`claude-fable-5-1`，13 條：keep 12／demote-v2 1／kill 0。
  - c 席 decisions：`claude-fable-5-1`，31 條：keep 29／demote-v2 1／kill 1。
  - web GPT Pro 席：`gpt-6-pro`（direct transport、材料模式、公司 ChatGPT 工作區、未接 DevSpace），44 條：keep 38／demote-v2 6／kill 0。
- Fable permit 已 issue、兩批各一次 consume、已 close。

## 非 keep 與分歧條目

| ID | 條目 | stories 席 | decisions 席 | Pro 席 | 處置 |
|---|---|---|---|---|---|
| F1 | 每次驗證估成本、超上限自動換層（US8＋分層驗證成本條） | demote-v2：原話只是當場要估算；且超上限改走整場比回放更貴，方向自相矛盾 | keep：防止背景默默花掉數百美元 | demote-v2：一次詢價不足以支持常態成本政策 | 上呈使用者，選 b：只顯示估算、不設上限、不自動換層 |
| F2 | 拒絕後只在重新累積到門檻才再提（US13＋常駐規則第 4 條） | keep：不處理會每輪永久重列 | keep：把拒絕時間當查詢起點即可 | demote-v2：沒有原話來源，首版只需結案 | 上呈使用者，選 a：從拒絕時間重新計算 |
| F3 | 回放與整場預設新舊各 3 次 | — | keep：只是參數預設 | demote-v2：3+3 出現在未被選定的選項 | 改為由第一張可行性工作單決定次數 |
| F4 | 回放只走原路由、結果檔不存整段前文 | — | kill：前半與模型路由條重複，後半未定價 | keep：收窄交付內容 | 路由限制併入模型路由條，保留「結果檔只存下一步與判斷」一行 |
| F5 | 隨機產生輸入、縮小失敗案例的屬性測試 | — | demote-v2：真實報告當固定案例即可抓到已量到的損失 | demote-v2：守恆斷言保留，隨機生成延後 | 改為兩份真實 cross 報告製成的去敏固定案例，P1–P3 寫成斷言；隨機屬性測試延到 v2 |
| F6 | 「需要真人回覆、私人資料、外部服務」一律視為回放做不到 | — | keep | keep，但指出材料未證明這類題材不能回放 | 回放用原對話紀錄，真人與外部回應已在其中；該條件移到整場測試層 |
| F7 | 第一張工作單（回放可行性）的來源標記 | — | 指出標錯：使用者選過 | 同左 | 改標使用者原話 |
| F8 | 整場測試層的來源標記 | — | — | 指出是使用者第 2 題原選項 b | 改標使用者原話，「重現不到判無效」註明為推論 |
| F9 | US3 改判附理由的來源 | 判 (c)：來自助理後補修法 | — | 判 a＋c | 補註「助理確認後提出、使用者未糾正」 |
| F10 | decisions 席指出兩個 evidence 標籤引用的數字（s06／s20 觀察、6/10 正確率）不在材料第三段 | — | 標籤不可核 | — | 材料缺口，不改 spec；下次審查材料要附 research.md 對應段落 |
| F11 | Pro 席分母檢查：多數損失未定價，通用驗證工具的建置規模與持續執行政策缺成本效益舉證 | — | — | 未定價 | 不另改 spec：未定價的部分已由 F1、F3、F5 收斂到最簡版，其餘條目受使用者原話保護 |

## 使用者拍板

- F1：「1.b」
- F2：「2.a」

## Dispositions

- disposition: F1 | tradeoff | main=tradeoff | user=accepted | 使用者選 b：只顯示估算、不設上限、不自動換層
- disposition: F2 | tradeoff | main=tradeoff | user=accepted | 使用者選 a：拒絕時間作為新起點，重新累積到門檻才再提
- disposition: F3 | actionable | main=actionable | user=n/a | 3+3 未被使用者選定，改由可行性工作單決定次數
- disposition: F4 | actionable | main=actionable | user=n/a | 合併重複條目，保留結果檔最小輸出限制
- disposition: F5 | actionable | main=actionable | user=n/a | 兩席一致延後隨機屬性測試，改固定案例
- disposition: F6 | actionable | main=actionable | user=n/a | 回放讀原對話紀錄，真人與外部回應已在其中
- disposition: F7 | actionable | main=actionable | user=n/a | 動工選項 a 原文含「第一張工作單先試回放」
- disposition: F8 | actionable | main=actionable | user=n/a | 第 2 題原選項 b 即整場測試
- disposition: F9 | actionable | main=actionable | user=n/a | 補註來源脈絡，不改條目內容
- disposition: F10 | noise | main=noise | user=n/a | 審查材料未附 research.md 對應段落；下輪材料補附即可
- disposition: F11 | immaterial | main=immaterial | user=n/a | F11：未定價部分已由 F1、F3、F5 收斂，其餘條目受原話保護
