# 02 — 把 routed recap 接進兩種 daily-local 入口

**What to build:** 關鍵字觸發與 `/daily-local` 都啟動 routed recap，並讓 `local-analysis` workflow 跳過內建 recap agent。main session 只在 routed recap 與 workflow 都有結果後整理 digest；失敗要進 failed，不可假裝完整。

**Blocked by:** 01 — 把 recap wrapper 改成原生 Opus／vendor Luna 分流

**Status:** completed

**Needs:** Ticket 1 的 routed recap wrapper；既有 hook、command 與 Workflow 契約測試。

**Validation method:** 先讓既有接線契約測試對新要求失敗，再修改 hook／command／Workflow。執行 hook/command 命中測試、agent contract validator 與不執行 recap 的接線 smoke test；枚舉兩個入口的 routed recap 命令與 workflow skip 參數逐字一致。

**Evidence required:** 兩個入口都含同一 routed recap 執行形狀；Workflow 在 external recap 模式不建立 recap agent call，回傳能辨認 external recap；既有排檔規則仍通過；所有契約測試與 validator exit 0；未執行完整 recap。

**TDD:** required

**TDD seam:** hook／command 對外產生的執行指示，以及 Workflow 接受 external recap 參數後的 due channel 清單與回傳結構。

- [x] 明天從關鍵字或 `/daily-local` 進場都使用 routed recap。— Source: Story 3
- [x] routed recap 與其餘 channel 完成前不產 digest，recap 失敗會列入 failed。— Source: Story 5
- [x] 只有 recap 改路由，其他 channel 的模型選擇不變。— Source: Implementation Decision 7
- [x] 正式流程不新增 free(max) fallback，不修改 Workflow watchdog。— Source: Out of Scope

## Verification Log

- RED：Workflow、hook、command 新接線測試皆先失敗；fresh command runner 回 `RECAP_ROUTE=missing`。
- GREEN：Workflow external recap 與既有 report contract PASS；hook `pass=59 fail=0`；command contract PASS；fresh command runner 與等待壓力情境均 PASS；agent contract validator `status=pass errors=0`。
- Review 修正：command 明確傳 `external_recap:true`，避免外部與內建 recap 重複執行；Workflow test 補 production wiring 與 return 欄位斷言。focused tests PASS。
- 完整模組測試：21 個測試檔中 20 個通過；未修改的 beads-aging 在 feature base 與目前版本均為 `22 PASS / 4 FAIL`，記為既有失敗，未納入本票修正。
- YAGNI：本票正式接線均 keep；拿掉新增但不影響驗收的 `OBSERVABILITY` unknown。其他 kill 是別的 session 既有變更，未碰。
