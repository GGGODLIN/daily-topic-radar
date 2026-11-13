# Known Issues — auto-updated by social_info pipeline

> Last updated: 2026-11-13 04:14 (Asia/Taipei)
> 來源 source 上次 fetch 失敗的最終狀態。pipeline 已自動 retry transient errors，出現在這裡的代表 retry 配額耗盡或屬於需要人介入的類別。

## 🚨 User action required (4)

- **twitter_tier1** (user_action_required) — HTTPStatusError: Client error '401 Unauthorized' for url 'http://127.0.0.1:8788/v2/acts/scrape.badger~twitter-tweets-scraper/run-sync-get-dataset-items?maxTotalChargeUsd=0.01'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401
  - last ok: 2026-11-12 04:00 CST · consecutive fails: 1 · last attempts: 1
  - → X 失敗看 error 文字分辨：(1)「0 usable tweets」= ScrapeBadger 有回列，但沒有可解析推文（mock_tweet，或缺少 id / username / text）。不是 Kaito 為湊最低收費塞的假資料，fetcher 也不會改打第二種 query。(2) HTTP 錯誤 / ReadError / timeout = 該請求失敗，source 內不會自動重打。每個 handle 各一發，已經打出去的不退費，不要盲目重跑整包。
- **twitter_radar** (user_action_required) — HTTPStatusError: Client error '401 Unauthorized' for url 'http://127.0.0.1:8788/v2/acts/scrape.badger~twitter-tweets-scraper/run-sync-get-dataset-items?maxTotalChargeUsd=0.01'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401
  - last ok: 2026-11-12 04:00 CST · consecutive fails: 1 · last attempts: 1
  - → 401/403 — 多半是 VPN / API key / 認證問題，需要你介入。
- **twitter_anthropic** (user_action_required) — HTTPStatusError: Client error '401 Unauthorized' for url 'http://127.0.0.1:8788/v2/acts/scrape.badger~twitter-tweets-scraper/run-sync-get-dataset-items?maxTotalChargeUsd=0.01'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401
  - last ok: 2026-11-12 04:00 CST · consecutive fails: 1 · last attempts: 1
  - → X 失敗看 error 文字分辨：(1)「0 usable tweets」= ScrapeBadger 有回列，但沒有可解析推文（mock_tweet，或缺少 id / username / text）。不是 Kaito 為湊最低收費塞的假資料，fetcher 也不會改打第二種 query。(2) HTTP 錯誤 / ReadError / timeout = 該請求失敗，source 內不會自動重打。每個 handle 各一發，已經打出去的不退費，不要盲目重跑整包。
- **twitter_matt** (user_action_required) — HTTPStatusError: Client error '401 Unauthorized' for url 'http://127.0.0.1:8788/v2/acts/scrape.badger~twitter-tweets-scraper/run-sync-get-dataset-items?maxTotalChargeUsd=0.01'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401
  - last ok: 2026-11-12 04:00 CST · consecutive fails: 1 · last attempts: 1
  - → 401/403 — 多半是 VPN / API key / 認證問題，需要你介入。

## 🪦 Stable failures (≥7 連續失敗) (1)

- **venturebeat_ai** (transient) — HTTPStatusError: Client error '429 Too Many Requests' for url 'https://venturebeat.com/category/ai/feed/'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429
  - last ok: 2026-09-03 06:00 CST · consecutive fails: 75 · last attempts: 4
  - → 暫時性錯誤 — 自動 retry 用完仍失敗，下次 run 會再試。

## ⏳ Transient — retry 用完仍失敗、下次 run 會再試 (1)

- **zhihu_hot** (transient) — ReadTimeout: 
  - last ok: 2026-11-12 04:00 CST · consecutive fails: 1 · last attempts: 4
  - → 暫時性錯誤 — 自動 retry 用完仍失敗，下次 run 會再試。

