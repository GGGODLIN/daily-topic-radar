# skills.sh radar YAGNI review

## 結論

- 席位：b（Gemini 3.7 Flash High）＋ c（本 session fresh routed-judge）。
- 分母：未定價；過去漏看 skill 的次數、損失與 skills.sh／GitHub repo 榜重疊率均未量測，因此 v1 以上限為最簡版。
- 共識：功能主體值得保留；不建立歷史資料庫、自訂分數、瀏覽器自動化或新監看架構。
- 分歧：Gemini 全數 keep；fresh judge 將「榜單清單與 per-board limit 配置化」兩項判為 demote-v2。
- 作者裁決：接受兩項 demote-v2，v1 固定抓 Trending＋Hot，只沿用單一 `limit` 值。
- 零 dispute，無需使用者追加拍板。

## 最簡版

1. 單一 skills.sh source 固定抓 Trending 與 Hot，兩榜共用 `limit: 25`。
2. 以 HTTP 解析榜單 HTML，正規化為既有 Item。
3. 沿用 canonical URL dedup、state.db、resurface、failure／empty 與 `also seen at`。
4. raw report 增加單一「Agent Skills (skills.sh)」H2，保留 Trending 在前、Hot 在後的榜單順序。
5. 顯示榜別、rank、installs 與發布來源；不臆造 description。
6. 測 fixture parser、renderer、雙榜 dedup 與空 HTML；完成後跑一次真站 smoke test。

## 逐條裁決

| # | 條目 | 來源類 | b | c | 作者裁決 |
|---|---|---|---|---|---|
| US-1 | 每天看 Trending 新 skill | 使用者原話 | keep | keep | keep；修正錯植引文 |
| US-2 | 同時看 Hot | 使用者原話 | keep | keep | keep |
| US-3 | 沿用 repo 榜管線 | 使用者原話 | keep | keep | keep |
| US-4 | skills.sh 獨立區塊 | 使用者原話 | keep | keep | keep |
| US-5 | 顯示榜別與 rank | 模型推導 | keep | keep | keep；排行語意必要 |
| US-6 | 顯示 installs | 模型推導 | keep | keep | keep；現成低成本訊號 |
| US-7 | 雙榜走 dedup／also seen | 使用者確認 | keep | keep | keep |
| US-8 | 沿用 state.db／resurface | 使用者原話 | keep | keep | keep |
| US-9 | 沿用 failure／empty 診斷 | 模型推導 | keep | keep | keep；不新增機制 |
| US-10 | Stage-2 能讀新區塊 | 模型推導 | keep | keep | keep；被動沿用 raw input，不改 workflow |
| US-11 | 限制榜單抓取數量 | 模型推導 | keep | demote-v2 | accept；只留單一 limit，不做 per-board 配置 |
| US-12 | 結構失配回空項目 | 模型推導 | keep | keep | keep |
| US-13 | HTML fixture 測試 | 使用者確認 | keep | keep | keep |
| US-14 | 真站 smoke test | 使用者確認 | keep | keep | keep |
| ID-1 | 單一 source type 抓兩榜 | 模型推導 | keep | keep | keep |
| ID-2 | 榜單清單＋per-board limit 配置 | 模型推導 | keep | demote-v2 | accept；v1 固定兩榜、共用單一 limit |
| ID-3 | limit 25／榜 | 既有慣例證據 | keep | keep | keep；只視為合理起點，不宣稱最佳值 |
| ID-4 | 直接解析 HTML | 實測證據 | keep | keep | keep |
| ID-5 | skill URL 作 canonical URL | 模型推導 | keep | keep | keep |
| ID-6 | handle 保存榜別與 rank | 模型推導 | keep | keep | keep |
| ID-7 | 發布來源進 author、不臆造摘要 | 實測證據 | keep | keep | keep |
| ID-8 | engagement 保存 rank／installs | 模型推導 | keep | keep | keep |
| ID-9 | 保留榜單順序 | 模型推導 | keep | keep | keep |
| ID-10 | 單一 Agent Skills H2 | 使用者確認 | keep | keep | keep |
| ID-11 | 不建歷史表／專用 DB | 使用者確認 | keep | keep | keep |
| ID-12 | 兩榜請求隔離 | 模型推導 | keep | keep | keep；迴圈內局部失敗即可 |
| ID-13 | 接既有 registry／raw 群組 | 模型推導 | keep | keep | keep；修正偽造 user provenance 為 inferred |
| ID-14 | README 同步 source type | 模型推導 | keep | keep | keep |

## 已修改 spec

- US-1 改用真正支撐 Trending 需求的使用者原話。
- US-11 移除「每榜可設定」語意，只要求限制抓取數量。
- ID-2 改為固定 Trending＋Hot，共用單一 limit；per-board limit 延後到有實際需求時。
- ID-13 的 assistant 摘要不再偽標為使用者逐字原話，改為 `[inferred]`。

## 會推翻本結論的證據

- 若量測顯示 skills.sh 榜與現有 GitHub repo 榜高度重疊、幾乎沒有新增發現，應重新評估整個 source 的價值。
- 若單一 limit 無法兼顧 Trending 與 Hot，才升級 per-board limit。
