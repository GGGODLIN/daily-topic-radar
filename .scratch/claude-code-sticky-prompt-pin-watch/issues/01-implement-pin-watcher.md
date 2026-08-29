# 01 — 實作 Claude Code pin watcher

**What to build:** 讓既有 tool-updates detector 在 Claude Code 暫時 pin 期間檢查指定 issue、官方 CHANGELOG 與候選 release。沒有修復訊號時不產生 Claude Code finding；有修復訊號且存在可測候選版本時，只提醒人工重新驗證，不自動安裝或解除 pin。

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

**Needs:** GitHub CLI 可查公開 repository；其餘項目 worker 可自行檢查。

**TDD:** required

**TDD seam:** tool-updates shell channel 的 `--json` 輸出

- [x] 先加入會失敗的 fixture 測試，證明 issues open 且 CHANGELOG 無修復訊號時沒有 Claude Code update finding。
- [x] fixture 證明任一受監控 issue closed 且存在候選 release 時產生「pin 可以重新驗證」finding。
- [x] fixture 證明 issues open、CHANGELOG 命中 sticky prompt 或 issue 編號且存在候選 release時產生 finding。
- [x] fixture 證明 GitHub 查詢失敗只進 errors，不產生修復 finding，exit code 維持成功。
- [x] 實作只加入本次 Claude Code pin 的專用檢查，不新增通用 manifest manager。
- [x] finding 不執行安裝、不移除 DISABLE_AUTOUPDATER、不宣稱候選版本已修復。

## Verification Log

- 2026-08-29 criterion correction：第一版掃描整份 CHANGELOG 且把任意 `sticky` 當訊號，真實 probe 因舊條目 `sticky across sessions` 誤報。新增 RED fixture 後，改成只掃版本高於 2.1.246 的候選 release 區段，並只接受精確 `sticky prompt` 或受監控 issue 編號；真實 probe 回到零 finding、零 pin error。
- 2026-08-29 review fix：尾端空 `## ` 區段原會讓 `splitlines()[0]` 丟 `IndexError`，新增 RED fixture 後改為先檢查 lines；focused test 回到全綠。
- 2026-08-29 review fix：top-5 release 查詢會讓較早候選版本的修復訊號被後續版本擠出。新增「第六筆候選含修復訊號」RED fixture，將單次查詢擴為 100 筆後轉綠；真實 probe 維持零 finding、零 pin error。
- Minor：CHANGELOG 文字路徑依賴官方維持 `## <版本>` heading；格式改變可能漏掉文字訊號。issue closure 與 2026-09-05 人工 trial review 仍是獨立偵測路徑，本票不擴張成通用格式探測器。
- Minor：未另測 issue 已關閉但尚無高於 pin 的 candidate release、issue 編號前綴撞更長編號、重複命中文字與測 9／10 fixture 對齊；現有 code 在前兩者只會靜默或多提醒，不會自動升版，依 scope 政策保留為已知限制。
