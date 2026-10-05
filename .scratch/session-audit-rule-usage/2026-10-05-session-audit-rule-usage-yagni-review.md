# YAGNI review — session-audit 規則使用量（2026-10-05）

## Run

- spec：`.scratch/session-audit-rule-usage/spec.md`（審查時版本 = commit b14fc9c）
- packet：8,786 字，sha256 `124f3ac597497916397f3f4d8adba8ee6f92f5e83a4a006dd0b3e0f3a0ebc81f`
- 使用者選單選擇：`c`（c 席＋web GPT Pro 席・fable 級）；c 席因 permit close 提早刪 snapshot 失敗一次，使用者重選 `a`（c 席・fable 級）後重派
- 席位與實際模型：
  - c 席 stories：`claude-fable-5-1`，17 列：keep 14／demote-v2 3／kill 0 → `yagni-seats/c-stories.md`
  - c 席 decisions：`claude-fable-5-1`，24 列：keep 20／demote-v2 4／kill 0 → `yagni-seats/c-decisions.md`
  - web GPT Pro：`gpt-6-pro`（renderer 回 `resolvedModelSlug=gpt-6-pro`），55 列：keep 46／demote-v2 4／kill 5 → `yagni-seats/pro.md`；controller 回 `ambiguous_submission`（answer-timeout-after-submit），以送出時間 10:00:24Z 對回 conversation `6ac3753f-be10-83ee-a1dc-a31cc6e8e7b3`（create 10:00:32Z、update 10:17:08Z）用 advisor-fetch-conversation 取回，未重送
- **限制：Pro 席 CLAIM 隔離不成立**。direct 路徑會附上本 session 的 transcript 快照（4,250,152 bytes／2,273 行），Pro 讀得到 main 先前的推薦與討論；它每條 verdict 都附 packet 內引文位置，但不能當成盲審。
- 前輪 review：查無前輪紀錄（本目錄第一份 review 檔）

## 三席共識（已改進 spec）

| ID | 條目 | 三席 | 改了什麼 |
|---|---|---|---|
| F1 | 零使用規則附模型三分類（原 US9、D10 分類子句） | stories demote／decisions demote／Pro kill | 刪除 US9 與分類子句；清單改附原文、commit、觀察期間、最近遇到時間；Out of Scope 記 v2 條件 |
| F2 | 每規則×每週完整次數表（Solution、原 D9） | stories demote／decisions demote（保留週計數）／Pro demote | 改成「最小狀態」：每規則最後遇到場合時間＋每週成功段數；成功段數 0 的週算沒覆蓋 |
| F3 | 「覆蓋是否足夠」未定義門檻（原 D9、T7） | decisions demote／Pro 改為「缺資料不是零、不能拼週」 | 沒覆蓋的週打斷連續計數；測試改成「0 段的週打斷計數」 |
| F4 | 問題未定價 | 三席皆判未定價 | Solution 加一句「未定價、以最簡版為上限」；Further Notes 加停損條件（+30 天後仍沒人跑 /rules-slim → 零使用出口降 v2 或停） |

## 單席意見，main 判 actionable 直接改

| ID | 條目 | 來源席 | 改了什麼／依據 |
|---|---|---|---|
| F5 | Solution 寫「已上線」與部署中狀態衝突 | Pro（P2 kill） | 改成「仍在部署中，正式啟用並有真實輸出後才接」；依據 spec 自己的 Further Notes 第 1 點 |
| F6 | 排除整個 `/var/folders` 超出證據 | Pro（D6.1 kill） | 收窄為 `-T-skill-up-` 特徵；依據 Part 3 只證明 skill-up |
| F7 | commit 時間 ≠ 規則生效時間 | Pro（D4.1 kill） | D4 改稱「最佳候選版本」並要求標出用哪個 commit 判 |
| F8 | 版本每段查一次過細 | decisions（D4 簡化） | 改為每 session 查一次 |
| F9 | 索引退路的「違規才二次送原文」 | Pro（D2.1 demote） | 刪除，等真的退回索引時再設計 |
| F10 | 累加與「不改寫既有行」保護衝突 | decisions（實作提醒） | D7 明定首行不改、違規以縮排子行追加，次數由子行數得出 |
| F11 | 規則摩擦的準確率免責沒寫到 | stories（S-2 反方證據） | Out of Scope 準確率條補上「違規判斷也可能錯（同類抽查 6/10）」 |
| F12 | 零使用要對照完整規則名冊，不能只看被回報過的 | Pro（零使用出口段） | 零使用清單決策與測試補上這點 |

## 不採納

| ID | 條目 | 來源席 | 理由 |
|---|---|---|---|
| F13 | 版本保護改成「規則改動後的舊段落一律標未分析、不做 git 回溯」 | stories（最簡版第 3 點） | decisions 與 Pro 都 keep git 回溯；改成一律標未分析會讓每次改規則後一大段歷史失去覆蓋 |

## 上呈使用者拍板

| ID | 條目 | 分歧 |
|---|---|---|
| F14 | `/rules-slim` 要不要加「零使用」正式判斷標準、允許用新證據重開 8/25 判「留」的條目 | stories：不改 /rules-slim／Pro：只接讀取、新判準與重開 demote／decisions：keep，理由是不重開的話零使用規則多半落在 8/25「留」的集合、清單對 /rules-slim 無效 |
| F15 | 規則原文被改寫就算新規則、零使用計數重算 | decisions：demote（常改規則→永遠到不了 4 週）／Pro：keep（保守、比跨版語意對應簡單） |

## Dispositions

- disposition: F1 | actionable | main=actionable | user=n/a | 三席一致砍分類，已改 spec
- disposition: F2 | actionable | main=actionable | user=n/a | 三席一致砍完整週表，改最小狀態
- disposition: F3 | actionable | main=actionable | user=n/a | 覆蓋門檻改為 0 段即沒覆蓋、打斷連續
- disposition: F4 | actionable | main=actionable | user=n/a | 未定價，加上限句與停損條件
- disposition: F5 | actionable | main=actionable | user=n/a | 已上線字樣更正
- disposition: F6 | actionable | main=actionable | user=n/a | 排除範圍收窄到 skill-up
- disposition: F7 | actionable | main=actionable | user=n/a | 版本改稱最佳候選並標 commit
- disposition: F8 | actionable | main=actionable | user=n/a | 版本查詢改每 session 一次
- disposition: F9 | actionable | main=actionable | user=n/a | 刪預設的二次送原文機制
- disposition: F10 | actionable | main=actionable | user=n/a | 子行追加取代改寫首行
- disposition: F11 | actionable | main=actionable | user=n/a | 準確率免責補違規判斷
- disposition: F12 | actionable | main=actionable | user=n/a | 零使用對照完整名冊
- disposition: F13 | noise | main=noise | user=n/a | 兩席 keep git 回溯，單席簡化會損失歷史覆蓋
- disposition: F14 | tradeoff | main=tradeoff | user=accepted | 選 a：/rules-slim 只接讀取，不加判準、不放寬不重判規矩（2026-10-05「可以照推薦」）
- disposition: F15 | tradeoff | main=tradeoff | user=accepted | 選 a：規則改寫即重算 4 週（2026-10-05「可以照推薦」）
