# 01 — 日期輸入 fail-fast

**What to build:** 四個 daily-topic workflow 在缺少日期、格式錯誤或不存在的日曆日期時，在任何 agent 啟動前回傳結構化輸入錯誤；合法日期維持既有 workflow 路徑，且不再執行 `unknown-date` fallback。

**Blocked by:** None — can start immediately

**Status:** completed

**Needs:** None — a worker can check every item unaided

**TDD:** `required`

**TDD seam:** Workflow invocation 的結構化 result 與第一個 agent call 是否發生

- [x] 合法的 `YYYY-MM-DD` 日期（包含有效 leap-day）會通過輸入 gate。
- [x] 缺少 date 時回傳 `aborted=true`、`abort_stage=input_validation`、具體原因與要求傳入合法日期的 next action。
- [x] 格式錯誤與不存在的日曆日期同樣在第一個 agent 前中止。
- [x] 缺少或錯誤日期不會呼叫任何 agent，也不會產生 `unknown-date` 產物。
- [x] default、full、minimal、vendor 四個 workflow 的行為一致。
- [x] 既有 object args 與 stringified args 的合法輸入行為維持不變。
