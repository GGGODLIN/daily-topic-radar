# Known Issues — auto-updated by social_info pipeline

> Last updated: 2026-09-22 06:04 (Asia/Taipei)
> 來源 source 上次 fetch 失敗的最終狀態。pipeline 已自動 retry transient errors，出現在這裡的代表 retry 配額耗盡或屬於需要人介入的類別。

## 🛠 Persistent error — fetcher 需要更新 (1)

- **twitter_matt** (persistent_error) — RuntimeError: actor returned 15 records but 0 usable tweets (15 mock_tweet padding) — upstream X search found nothing for 1 handles over 36h across 2 request shapes (searchTerms, twitterContent)
  - last ok: 2026-09-21 06:00 CST · consecutive fails: 1 · last attempts: 1
  - → 4xx 持續錯誤 — 多半是 source schema / API 變更，需要你介入更新 fetcher。

## 🪦 Stable failures (≥7 連續失敗) (1)

- **venturebeat_ai** (transient) — HTTPStatusError: Client error '429 Too Many Requests' for url 'https://venturebeat.com/category/ai/feed/'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429
  - last ok: 2026-09-03 06:00 CST · consecutive fails: 22 · last attempts: 4
  - → 暫時性錯誤 — 自動 retry 用完仍失敗，下次 run 會再試。

