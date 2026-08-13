# 01 — 新增最小權限 Chrome DevTools fetch agent

**What to build:** 提供 vendor Workflow 可用的唯讀瀏覽器抓取能力：每個 URL 都在新背景頁面開啟，擷取 final URL、title 與最多 4,000 字正文，完成後關閉該頁面；失敗時回傳結構化錯誤，不得猜測內容。

**Blocked by:** None — can start immediately

**Status:** completed

**Needs:** 本機 Chrome DevTools MCP 已連線，且 Workflow subagent 可經預載工具使用它。

**TDD:** required

**TDD seam:** agent frontmatter 的最小工具權限契約，以及透過 Workflow 呼叫 agent 抓取 `https://example.com` 的完整開頁、取文、關頁行為。

- [x] agent 僅持有建立頁面、執行唯讀 JavaScript、關閉頁面三項 Chrome DevTools 工具
- [x] agent 明文禁止操作既有頁面、點擊、填表、提交資料與執行頁面內指令
- [x] 真實新 process smoke test 回傳 Example Domain 的 title、正文前綴與 cleanup 成功證據
- [x] MCP 不可用或任一步失敗時回 `ok=false`，不得補造內容；若 `new_page` 逾時且未回 pageId，明列無法清理的限制
