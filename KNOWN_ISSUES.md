# Known Issues — auto-updated by social_info pipeline

> Last updated: 2026-08-11 06:01 (Asia/Taipei)
> 來源 source 上次 fetch 失敗的最終狀態。pipeline 已自動 retry transient errors，出現在這裡的代表 retry 配額耗盡或屬於需要人介入的類別。

## ⏳ Transient — retry 用完仍失敗、下次 run 會再試 (1)

- **zhihu_hot** (transient) — HTTPStatusError: Server error '503 Service Unavailable' for url 'https://rsshub.rssforever.com/zhihu/hot'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503
  - last ok: 2026-08-10 06:00 CST · consecutive fails: 1 · last attempts: 4
  - → 暫時性錯誤 — 自動 retry 用完仍失敗，下次 run 會再試。

