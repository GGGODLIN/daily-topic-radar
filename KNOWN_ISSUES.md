# Known Issues — auto-updated by social_info pipeline

> Last updated: 2026-11-24 04:14 (Asia/Taipei)
> 來源 source 上次 fetch 失敗的最終狀態。pipeline 已自動 retry transient errors，出現在這裡的代表 retry 配額耗盡或屬於需要人介入的類別。

## 🪦 Stable failures (≥7 連續失敗) (5)

- **reddit_localllama** (persistent_error) — HTTPStatusError: Client error '404 Not Found' for url 'https://www.reddit.com/r/LocalLLaMA/top.rss?t=day&limit=10' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404
  - last ok: 2026-11-17 04:00 CST · consecutive fails: 7 · last attempts: 1
  - → fetcher 抓 www.reddit.com/r/X/top.rss（官方自助 API 已關閉、old.reddit 列表頁亦已封鎖）。若回 403 多半是雲端 / VPN 出口 IP 被擋，關掉 VPN 後跑 `uv run python -m social_info --retry-failures` 補抓；若回 429 為 RSS 節流，下次排程會依退避序列重試。
- **reddit_claudeai** (persistent_error) — HTTPStatusError: Client error '404 Not Found' for url 'https://www.reddit.com/r/ClaudeAI/top.rss?t=day&limit=10' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404
  - last ok: 2026-11-17 04:00 CST · consecutive fails: 7 · last attempts: 2
  - → fetcher 抓 www.reddit.com/r/X/top.rss（官方自助 API 已關閉、old.reddit 列表頁亦已封鎖）。若回 403 多半是雲端 / VPN 出口 IP 被擋，關掉 VPN 後跑 `uv run python -m social_info --retry-failures` 補抓；若回 429 為 RSS 節流，下次排程會依退避序列重試。
- **reddit_openai** (persistent_error) — HTTPStatusError: Client error '404 Not Found' for url 'https://www.reddit.com/r/OpenAI/top.rss?t=day&limit=10' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404
  - last ok: 2026-11-17 04:00 CST · consecutive fails: 7 · last attempts: 1
  - → fetcher 抓 www.reddit.com/r/X/top.rss（官方自助 API 已關閉、old.reddit 列表頁亦已封鎖）。若回 403 多半是雲端 / VPN 出口 IP 被擋，關掉 VPN 後跑 `uv run python -m social_info --retry-failures` 補抓；若回 429 為 RSS 節流，下次排程會依退避序列重試。
- **reddit_singularity** (persistent_error) — HTTPStatusError: Client error '404 Not Found' for url 'https://www.reddit.com/r/singularity/top.rss?t=day&limit=10' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404
  - last ok: 2026-11-17 04:00 CST · consecutive fails: 7 · last attempts: 1
  - → fetcher 抓 www.reddit.com/r/X/top.rss（官方自助 API 已關閉、old.reddit 列表頁亦已封鎖）。若回 403 多半是雲端 / VPN 出口 IP 被擋，關掉 VPN 後跑 `uv run python -m social_info --retry-failures` 補抓；若回 429 為 RSS 節流，下次排程會依退避序列重試。
- **reddit_machinelearning** (persistent_error) — HTTPStatusError: Client error '404 Not Found' for url 'https://www.reddit.com/r/MachineLearning/top.rss?t=day&limit=10' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404
  - last ok: 2026-11-17 04:00 CST · consecutive fails: 7 · last attempts: 1
  - → fetcher 抓 www.reddit.com/r/X/top.rss（官方自助 API 已關閉、old.reddit 列表頁亦已封鎖）。若回 403 多半是雲端 / VPN 出口 IP 被擋，關掉 VPN 後跑 `uv run python -m social_info --retry-failures` 補抓；若回 429 為 RSS 節流，下次排程會依退避序列重試。

## ⏳ Transient — retry 用完仍失敗、下次 run 會再試 (5)

- **twitter_tier1** (transient) — HTTPStatusError: Server error '503 Service Unavailable' for url 'http://127.0.0.1:8788/v2/acts/scrape.badger~twitter-tweets-scraper/run-sync-get-dataset-items?maxTotalChargeUsd=0.01' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503
  - last ok: 2026-11-21 04:00 CST · consecutive fails: 3 · last attempts: 4
  - → X 失敗看 error 文字分辨：(1)「0 usable tweets」= ScrapeBadger 有回列，但沒有可解析推文（mock_tweet，或缺少 id / username / text）。不是 Kaito 為湊最低收費塞的假資料，fetcher 也不會改打第二種 query。(2) HTTP 錯誤 / ReadError / timeout = 該請求失敗，source 內不會自動重打。每個 handle 各一發，已經打出去的不退費，不要盲目重跑整包。
- **twitter_radar** (transient) — HTTPStatusError: Server error '503 Service Unavailable' for url 'http://127.0.0.1:8788/v2/acts/scrape.badger~twitter-tweets-scraper/run-sync-get-dataset-items?maxTotalChargeUsd=0.01' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503
  - last ok: 2026-11-21 04:00 CST · consecutive fails: 3 · last attempts: 4
  - → X 失敗看 error 文字分辨：(1)「0 usable tweets」= ScrapeBadger 有回列，但沒有可解析推文（mock_tweet，或缺少 id / username / text）。不是 Kaito 為湊最低收費塞的假資料，fetcher 也不會改打第二種 query。(2) HTTP 錯誤 / ReadError / timeout = 該請求失敗，source 內不會自動重打。每個 handle 各一發，已經打出去的不退費，不要盲目重跑整包。
- **twitter_practitioners** (transient) — HTTPStatusError: Server error '503 Service Unavailable' for url 'http://127.0.0.1:8788/v2/acts/scrape.badger~twitter-tweets-scraper/run-sync-get-dataset-items?maxTotalChargeUsd=0.01' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503
  - last ok: 2026-11-21 04:00 CST · consecutive fails: 3 · last attempts: 4
  - → 暫時性錯誤 — 自動 retry 用完仍失敗，下次 run 會再試。
- **twitter_anthropic** (transient) — HTTPStatusError: Server error '503 Service Unavailable' for url 'http://127.0.0.1:8788/v2/acts/scrape.badger~twitter-tweets-scraper/run-sync-get-dataset-items?maxTotalChargeUsd=0.01' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503
  - last ok: 2026-11-21 04:00 CST · consecutive fails: 3 · last attempts: 4
  - → X 失敗看 error 文字分辨：(1)「0 usable tweets」= ScrapeBadger 有回列，但沒有可解析推文（mock_tweet，或缺少 id / username / text）。不是 Kaito 為湊最低收費塞的假資料，fetcher 也不會改打第二種 query。(2) HTTP 錯誤 / ReadError / timeout = 該請求失敗，source 內不會自動重打。每個 handle 各一發，已經打出去的不退費，不要盲目重跑整包。
- **twitter_matt** (transient) — HTTPStatusError: Server error '503 Service Unavailable' for url 'http://127.0.0.1:8788/v2/acts/scrape.badger~twitter-tweets-scraper/run-sync-get-dataset-items?maxTotalChargeUsd=0.01' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503
  - last ok: 2026-11-21 04:00 CST · consecutive fails: 3 · last attempts: 4
  - → X 失敗看 error 文字分辨：(1)「0 usable tweets」= ScrapeBadger 有回列，但沒有可解析推文（mock_tweet，或缺少 id / username / text）。不是 Kaito 為湊最低收費塞的假資料，fetcher 也不會改打第二種 query。(2) HTTP 錯誤 / ReadError / timeout = 該請求失敗，source 內不會自動重打。每個 handle 各一發，已經打出去的不退費，不要盲目重跑整包。

