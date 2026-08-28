## Problem Statement

目前 daily radar 能從 GitHub Trending、Trendshift 與 GitHub Search 發現熱門或上升中的 repo，也能檢查本機已安裝 skill 的品質與健康狀態，但無法從 skills.sh 發現熱門或快速升溫的單一 skill。使用者因此可能看到 skill repo，卻漏掉 repo 內真正受關注的 skill。

## Solution

把 skills.sh Trending 與 Hot 榜視為既有 daily radar 的新外部資料源。每天抓取兩個榜單，將榜上 skill 正規化成既有 Item，沿用 fetch、state.db 去重、resurface、raw report 與 Stage-2 digest 管線。raw report 新增單一「Agent Skills (skills.sh)」獨立區塊，並顯示榜別、rank 與 installs。

## User Stories

1. 身為 daily radar 使用者，我想每天看到 skills.sh Trending 榜的新 skill，以便在它們快速升溫時發現它們。[user: "目前我有在盯github repo的榜，但好像沒在盯skill的？有skill的榜嗎？"]
2. 身為 daily radar 使用者，我想同時看到 skills.sh Hot 榜，以免只靠 Trending 漏掉另一種熱門訊號。[user: "Hot也可以看"]
3. 身為 daily radar 使用者，我想讓 skill 榜沿用目前 repo 榜的資料管線，以免維護另一套監看架構。[user: "那機制可以跟目前的repo一樣"]
4. 身為 daily radar 使用者，我想讓 skills.sh 項目出現在獨立區塊，以便它們不與 GitHub repo 混在一起。[user: "也跟repo一樣獨立區塊"]
5. 身為 daily radar 使用者，我想在每個 skill 項目看到其榜別與 rank，以便理解它為何出現在報告中。[inferred]
6. 身為 daily radar 使用者，我想在每個 skill 項目看到 installs，以便把人氣當作探索訊號。[inferred]
7. 身為 daily radar 使用者，我想讓同一 skill 同時出現在 Trending 與 Hot 時沿用既有跨來源去重與 `also seen at` 呈現，以免重複閱讀。[user: "可以"]
8. 身為 daily radar 使用者，我想讓曾看過的 skill 沿用 state.db 去重與 resurface 規則，以便報告維持與 repo 項目一致的節奏。[user: "那機制可以跟目前的repo一樣"]
9. 身為 daily radar 使用者，我想讓 skills.sh 抓取失敗或解析出零項時出現在既有 failure／empty 診斷中，以免資料源靜默失效。[inferred]
10. 身為 Stage-2 digest 使用者，我想讓 digest 能讀到獨立的 skills.sh 區塊，以便把重要 skill 納入當日主題分析。[inferred]
11. 身為維護者，我想限制兩個榜單的抓取數量，以免單日報告被榜單大量項目淹沒。[inferred]
12. 身為維護者，我想讓 parser 結構失配時回傳零項並觸發既有 empty 診斷，而不是產生錯誤 skill 資料。[inferred]
13. 身為維護者，我想以可重放的 HTML fixture 測試榜單解析，以免單元測試依賴即時網站內容。[user: "可以"]
14. 身為維護者，我想在實作完成後用真實 skills.sh 頁面跑一次 smoke test，以確認 fixture 與現況一致。[user: "可以"]

## Implementation Decisions

- 新增一種 skills.sh source type，由單一 source 設定同時抓取 Trending 與 Hot，避免為兩個榜單建立重複 fetcher。[user: "那機制可以跟目前的repo一樣"]
- source 固定抓取 Trending 與 Hot，沿用單一 limit 設定且兩榜使用相同值；v1 不提供榜單清單或 per-board limit。[inferred]
- 初始 limit 採每榜 25 項，對齊現有上升榜來源的規模；未來可只改設定調整。[evidence: 現有 Trendshift source 設定 limit=25]
- fetcher 直接讀取 skills.sh 榜單 HTML，從榜單列取得 rank、skill 名稱、發布來源、skill URL 與 installs；不依賴瀏覽器執行 JavaScript。[evidence: 2026-08-28 直接抓取 skills.sh Trending／Hot HTML 可取得榜單列]
- 每個 Item 使用 skills.sh skill 頁面作為 URL 與 canonical URL，讓同一 skill 在 Trending 與 Hot 間進入既有 URL 去重流程。[inferred]
- Item source 固定為 skills.sh source type；source handle 包含榜別與 rank，讓報告能區分 Trending 與 Hot。[inferred]
- 發布來源寫入 author 或等價既有欄位；榜單未提供可靠 description 時不臆造摘要。[evidence: 2026-08-28 榜單列可取得發布來源，但未提供 skill description]
- engagement 保存 rank 與 installs；markdown renderer 明確顯示兩者，不把它們誤標成 likes、comments 或通用 score。[inferred]
- skills.sh 區塊保留 fetcher 產生的榜單順序，避免通用 engagement 加總排序破壞 rank 語意；Trending 項目先於 Hot 項目。[inferred]
- raw report 新增單一「Agent Skills (skills.sh)」平台群組；Trending 與 Hot 不拆成兩個 H2 區塊。[user: "可以"]
- 同一 skill 的雙榜重複由既有 dedup／`also_appeared_in` 管線處理，不新增排行歷史表或專用資料庫。[user: "可以"]
- 兩個榜單各自隔離請求失敗：其中一榜成功時仍保留成功項目；錯誤沿用既有 source 執行紀錄與 stderr 診斷慣例。[inferred]
- 新 source 接入既有 fetcher registry、設定檔與 raw report 群組，不修改本機分析 workflow。[inferred]
- README 的 source type 清單同步加入新類型，避免設定文件落後實作。[inferred]

## Testing Decisions

- 主要測試 seam 是 fetcher contract：用固定 Trending 與 Hot HTML fixture 模擬 HTTP 回應，驗證 Item 數量、URL、榜別、rank、installs、發布來源與 limit。
- 第二個測試 seam 是 `render_file`：驗證 skills.sh 項目只出現在單一「Agent Skills (skills.sh)」獨立區塊，且 rank／installs 可讀。
- 測試同一 canonical URL 同時來自 Trending 與 Hot 時，沿用既有 dedup／`also seen at` 行為；不另測 state.db 已被既有測試覆蓋的通用規則。
- 測試 parser 面對不含榜單列的成功 HTML 時回傳空清單，讓既有 empty-source 診斷接手。
- 先跑新 fetcher 測試與 markdown 測試，再跑完整 pytest，因為變更會新增 registry 與 source type。
- 完成後以 `--source` 對新 source 跑一次真實 fetch smoke test，驗證實際輸出包含非零項目與獨立區塊；smoke test 不寫入正式 state.db 或當日正式 report。

## Out of Scope

- 不建立 skill 名次或 installs 的跨日歷史資料表。
- 不計算自訂成長率、Hot 分數或品質分數。
- 不抓 skills.sh 累積 Leaderboard 或 Official 分頁。
- 不自動安裝、更新、評估或推薦榜上 skill。
- 不修改本機 skill health、upstream、collision、trigger 或 evaluator channel。
- 不改 Stage-2 digest 的 lens、prompt 或 HTML 版型；新區塊沿用既有 raw report 輸入。
- 不為 skills.sh 建立瀏覽器自動化或第三方 API 依賴。

## Further Notes

skills.sh 的榜單 HTML 屬外部結構，fixture 測試只能防止本地 parser 無意退化，不能保證上游不改版。既有 empty-source 診斷與真實 fetch smoke test 共同負責偵測上游 drift。

## Verification Results

- 2026-08-28 完整 pytest：167 passed。
- 2026-08-28 最終真站 fetch：Trending 25 項、Hot 25 項；既有 dedup 後 49 項，其中 1 項保留另一榜的 `also seen at` provenance。榜單會即時變動，重疊數不視為固定契約。
- 真站渲染包含單一「Agent Skills (skills.sh)」區塊，以及可讀的 rank 與 installs。
- smoke verification 使用 temporary state.db 並只在記憶體渲染，未寫入正式 state.db 或當日 report。
