# Known Issues — auto-updated by social_info pipeline

> Last updated: 2026-09-06 06:04 (Asia/Taipei)
> 來源 source 上次 fetch 失敗的最終狀態。pipeline 已自動 retry transient errors，出現在這裡的代表 retry 配額耗盡或屬於需要人介入的類別。

## ⏳ Transient — retry 用完仍失敗、下次 run 會再試 (2)

- **venturebeat_ai** (transient) — HTTPStatusError: Client error '429 Too Many Requests' for url 'https://venturebeat.com/category/ai/feed/'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429
  - last ok: 2026-09-03 06:00 CST · consecutive fails: 5 · last attempts: 4
  - → 暫時性錯誤 — 自動 retry 用完仍失敗，下次 run 會再試。
- **zhihu_hot** (transient) — ReadTimeout: 
  - last ok: 2026-09-05 10:28 CST · consecutive fails: 1 · last attempts: 4
  - → 暫時性錯誤 — 自動 retry 用完仍失敗，下次 run 會再試。

