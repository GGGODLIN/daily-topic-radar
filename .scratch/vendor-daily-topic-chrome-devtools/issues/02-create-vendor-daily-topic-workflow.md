# 02 — 建立 vendor daily-topic workflow

**What to build:** 提供與目前預設每日話題分析同構的 vendor workflow，只把 `chrome_required` fact-check 執行器換成 Chrome DevTools fetch agent；保留所有 guard、模型槽、一般 URL 抓取、verify、PROBES writeback、WriteDigest 與四 lens DigestAudit，且不改預設觸發路由。

**Blocked by:** 01 — 新增最小權限 Chrome DevTools fetch agent

**Status:** completed

**Needs:** Ticket 01 的 agent 已可被 Workflow 依名稱派用。

**TDD:** required

**TDD seam:** vendor workflow 與預設 workflow 的靜態差異契約，以及既有三道 fail-fast guard 回歸測試。

- [x] vendor workflow 有獨立名稱與描述，清楚標示供非 Claude vendor session 手動執行
- [x] `chrome_required` 路徑使用新 agent 與 Chrome DevTools 操作流程
- [x] vendor workflow 的 `chrome_required` executor 不再使用既有 `chrome-fetcher`
- [x] 預設 workflow、完整版 workflow與預設 hook 均未修改
- [x] 除 workflow 身分與 Chrome executor 區域外，vendor 版與當前預設版保持同構
- [x] 既有 daily-topic guard 測試保持通過
