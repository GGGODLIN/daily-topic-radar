## Problem Statement

Claude Code 2.1.247 之後的 fullscreen sticky prompt header 出現 regression，因此目前暫時使用 2.1.246 並停用 auto-update。單靠 GitHub issue 狀態可能漏掉「新版已修好，但 issue 或 CHANGELOG 尚未同步」的情況；也不能讓一般新版通知每天重複要求解除刻意的 pin。

## Solution

擴充既有 daily-local tool-updates detector，替 Claude Code 暫時 pin 增加修復訊號檢查。一般情況保持靜默；issue 關閉或 CHANGELOG 出現修復訊號時，只提醒人工重新驗證，不自動升版。另建立七天觀察 trial，review 時即使 issue 沒更新，也要直接測試候選版本是否已恢復 sticky prompt。

## User Stories

1. As a Claude Code 使用者, I want 暫時保留 2.1.246, so that 我可以繼續使用 sticky prompt header。[user: "好像不行，還是退版吧"]
2. As a Claude Code 使用者, I want 停用背景 auto-update, so that regression 版本不會自動覆蓋目前可用版本。[evidence: `claude doctor` 顯示 DISABLE_AUTOUPDATER 已生效]
3. As a Claude Code 使用者, I want 既有 daily-local tool-updates 幫我監控修復訊號, so that 不必記得手動查看 issue。[user: "可以，就這樣做"]
4. As a Claude Code 使用者, I want issue 未變且 CHANGELOG 無修復訊號時完全靜默, so that 刻意 pin 不會每天被當成普通落後版本提醒。[inferred]
5. As a Claude Code 使用者, I want issue 關閉或 CHANGELOG 出現修復訊號時收到重新驗證提醒, so that 我能及時評估是否解除 pin。[user: "那我是不是要做個機制或提醒我更新？"]
6. As a Claude Code 使用者, I want 提醒只要求人工驗證而不自動升版, so that 未實測的版本不會直接取代目前可用版本。[evidence: sticky prompt 版本邊界尚未獲 Anthropic 官方確認]
7. As a Claude Code 使用者, I want 七天後一定 review 這個 pin, so that issue 沒更新時仍會檢查新版是否已偷偷修復。[user: "然後加一個trial掛七天後提醒我看這件事，避免issue沒有更新但偷偷修好了"]
8. As a maintainer, I want GitHub 查詢失敗時 graceful skip, so that 暫時性網路或 API 問題不會誤報已修復。[user: "可以"]
9. As a maintainer, I want 沿用既有 tool-updates JSON fixture seam, so that 新行為由既有最高層外部輸出驗證，不新增平行測試架構。[user: "可以"]

## Implementation Decisions

- 擴充既有 daily-local tool-updates detector，不接進 daily-topic digest，也不建立新的 digest pipeline。[evidence: tool-updates 已是 daily shell channel；daily-topic 是 stage-2 外部資訊分析]
- 在既有 detector 內加入單一 Claude Code pin 檢查，直接承載本次 pin 版本、upstream repo、issue 編號與修復文字條件；不新增通用 manifest manager。[inferred]
- 修復訊號包含任一受監控 issue 關閉，或官方 CHANGELOG 命中 sticky prompt／受監控 issue 編號。[user: "避免issue沒有更新但偷偷修好了"]
- CHANGELOG 只掃版本嚴格高於 2.1.246 的候選 release 區段，文字條件使用精確 `sticky prompt` 或受監控 issue 編號，避免舊版與無關的 `sticky` 文字造成永久誤報。[evidence: 2026-08-29 真實 probe 曾被歷史條目 `sticky across sessions` 誤觸發]
- 只有修復訊號存在且有不同於目前 pin 版本的候選 release 時，才輸出「Claude Code pin 可以重新驗證」。[inferred]
- 候選 release 查詢不得只看 top 5，否則較早版本的修復文字可能被快速發版擠出窗口；本次專用 watcher 單次讀取最多 100 筆 release。[evidence: 2026-08-29 code review 以第六筆候選修復訊號重現永久漏報]
- detector 只產生提醒，不執行安裝、不移除 DISABLE_AUTOUPDATER，也不宣稱候選版本已修好。[evidence: 使用者已採人工 pin；版本修復狀態仍需行為驗證]
- GitHub issue、release 或 CHANGELOG 查詢失敗時記錄 graceful error；不得把失敗當成修復訊號。[user: "可以"]
- trial review 日期固定為 2026-09-05。review 必須同時查 issue／CHANGELOG 與直接測試候選版本，issue 無變化不能作為跳過實測的理由。[user: "加一個trial掛七天後提醒我看這件事，避免issue沒有更新但偷偷修好了"]
- 不修改既有 KNOWN_ISSUES.md，也不納入 repo 目前其他未提交變更。[evidence: implementation scope instruction]

## Testing Decisions

- 沿用 tool-updates 現有 `--json` fixture seam，透過 stubbed `gh` 與 fixture manifest 驗證完整輸出，不測內部 helper 實作細節。
- 驗證兩個 issue 都 open、候選版本區段無修復文字時，不產生 Claude Code update finding；fixture 同時放入舊版 `sticky prompt` 與候選版無關的 `sticky across sessions` 作為誤報干擾。
- 驗證任一 issue closed 時，產生人工重新驗證 finding。
- 驗證 issue 仍 open、CHANGELOG 命中 sticky prompt 或受監控 issue 編號時，產生人工重新驗證 finding。
- 驗證修復文字位於第六筆候選 release 時仍產生 finding，避免固定 top-5 窗口漏報。
- 驗證尾端空 CHANGELOG heading 不會讓 detector 崩潰。
- 驗證 GitHub 查詢失敗時只進 errors，不產生修復 finding，且整體 exit code 維持成功。
- 驗證現有其他 manager 行為不變，執行既有單一測試檔作為局部 regression gate。
- trial ledger 修改後執行 active.md 檔首列出的契約測試，確認 H2、三欄索引與 detail identity 都符合格式。

## Out of Scope

- 自動安裝新版 Claude Code。
- 自動移除 DISABLE_AUTOUPDATER。
- 建立通用 GitHub issue watcher framework。
- 修改 daily-topic workflow。
- 修復 Claude Code sticky prompt regression 本身。
- 修改 KNOWN_ISSUES.md 或其他既有未提交內容。

## Further Notes

七天 trial 是 issue／CHANGELOG watcher 的 backstop，不是只觀察 detector 是否出現 finding。review 當天即使所有遠端狀態都沒變，也要取得候選版本並做 sticky prompt 行為驗證；若仍壞，延長 pin 或重新設定 review 日期。

2026-08-29 真實 read-only probe 結果為零 pin finding、零 pin error；這只證明當時遠端訊號未達提醒條件，不證明 2.1.251 已修復或仍故障。[evidence: `cc-tool-updates-daily.sh --json` 實際輸出]