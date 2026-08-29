# Claude Code sticky prompt pin watcher — YAGNI review

## 三句結論

1. 這輪審的是「值不值得做」，不是功能是否正確。
2. agy 與 c 席 decisions 一致認為：提醒與七天 trial 可保留，但為單一暫時 pin 新增通用 manifest manager 太早。
3. 目前只需決定是否接受內部簡化；這不改變提醒行為、trial 日期或人工驗證邊界。

## 席位狀態

- b 席 agy：valid。逐條覆蓋 9 stories + 8 decisions；keep 16、demote-v2 1、kill 0。
- c 席 stories：valid。逐條覆蓋 9 stories；keep 9、demote-v2 0、kill 0。
- c 席 decisions：valid。逐條覆蓋 8 decisions；keep 7、demote-v2 1、kill 0。

## 綜合逐條表

| # | 條目 | 來源類 | 綜合 verdict | 一句理由 |
|---|---|---|---|---|
| US1 | 暫時保留 2.1.246 | 使用者原話 | keep | 使用者已拍板退版。 |
| US2 | 停用 auto-update | 量測證據 | keep | `claude doctor` 已確認生效。 |
| US3 | 由 daily-local tool-updates 監控 | 使用者原話 | keep | 使用者核准既有掛載點。 |
| US4 | 無修復訊號時靜默 | 模型推導 | keep | 否則刻意 pin 會每天變成普通落後版本噪音。 |
| US5 | 修復訊號出現時提醒 | 使用者原話 | keep | 本次功能的核心需求。 |
| US6 | 只提醒、不自動升版 | 量測證據 | keep | 修復狀態仍需行為驗證。 |
| US7 | 七天後強制 review | 使用者原話 | keep | 防止 issue 沒更新但已偷偷修好。 |
| US8 | GitHub 查詢失敗 graceful skip | 使用者核准 | keep | 查詢失敗不得被當成修復訊號。 |
| US9 | 沿用既有 JSON fixture seam | 使用者核准 | keep | 不新增平行測試架構。 |
| D1 | 擴充 tool-updates、不接 daily-topic | 使用者核准＋既有架構證據 | keep | 復用既有 shell channel。 |
| D2 | 新增通用 manifest pin manager | 模型推導 | demote-v2 | 目前只有一個 pin；專用分支可完整滿足需求，第二個案例出現後再抽象。 |
| D3 | issue 關閉或 CHANGELOG 命中即為修復訊號 | 使用者原話 | keep | 同時涵蓋 issue 更新與偷偷修復。 |
| D4 | 有不同候選 release 才提醒 | 模型推導 | keep | 避免沒有可測版本時發出無效提醒。 |
| D5 | 不安裝、不移除 pin、不宣稱已修好 | 量測證據 | keep | 保留人工驗證邊界。 |
| D6 | 查詢失敗記 error、不視為修復 | 使用者核准 | keep | 防止 false positive。 |
| D7 | review 日固定 2026-09-05 且必須直接實測 | 使用者原話 | keep | issue 無變化不能跳過實測。 |
| D8 | 不碰 KNOWN_ISSUES 與其他未提交變更 | 範圍證據 | keep | 防止 scope 滾大。 |

## 分母檢查

- 結果：未定價。
- Spec 沒有「過去偷偷修好發生幾次」與「每次延遲造成多少損失」的歷史數據。
- 因此解法以上述單一專用檢查 + 一筆 trial 為上限，不建立通用 watcher framework。

## 最簡版

1. 保持 2.1.246 與 DISABLE_AUTOUPDATER。
2. 在既有 tool-updates detector 補一段 Claude Code 專用檢查：issues／CHANGELOG 無訊號時靜默；有訊號且有候選 release 時提醒人工驗證。
3. 新增 2026-09-05 到期的 trial；review 當天無論 issue 是否更新都直接測候選版本。
4. 沿用既有 `--json` fixture seam 測四種外部行為。

## Spec 修改

- accept D2：把「manifest 新增通用 Claude Code pin manager」改成「既有 detector 內的單一專用 pin 檢查」。
- 其餘條目 keep。
- 無需使用者裁決的 trade-off；修改不影響 scope、對外行為或風險承擔。

## 來源報告

- agy：背景 task `buf70eky3`
- c stories：`/Users/linhancheng/.claude/.scratch/yagni-c-stories-cf4f5877-76aa-4bbc-bbd9-1ed35c72ddc0.report.md`
- c decisions：`/Users/linhancheng/.claude/.scratch/yagni-c-decisions-cf4f5877-76aa-4bbc-bbd9-1ed35c72ddc0.report.md`
