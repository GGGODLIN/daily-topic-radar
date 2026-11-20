# Known Issues — auto-updated by social_info pipeline

> Last updated: 2026-11-20 04:16 (Asia/Taipei)
> 來源 source 上次 fetch 失敗的最終狀態。pipeline 已自動 retry transient errors，出現在這裡的代表 retry 配額耗盡或屬於需要人介入的類別。

## 🛠 Persistent error — fetcher 需要更新 (5)

- **reddit_localllama** (persistent_error) — HTTPStatusError: Client error '404 Not Found' for url 'https://www.reddit.com/r/LocalLLaMA/top.rss?t=day&limit=10' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404
  - last ok: 2026-11-17 04:00 CST · consecutive fails: 3 · last attempts: 1
  - → fetcher 抓 www.reddit.com/r/X/top.rss（官方自助 API 已關閉、old.reddit 列表頁亦已封鎖）。若回 403 多半是雲端 / VPN 出口 IP 被擋，關掉 VPN 後跑 `uv run python -m social_info --retry-failures` 補抓；若回 429 為 RSS 節流，下次排程會依退避序列重試。
- **reddit_claudeai** (persistent_error) — HTTPStatusError: Client error '404 Not Found' for url 'https://www.reddit.com/r/ClaudeAI/top.rss?t=day&limit=10' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404
  - last ok: 2026-11-17 04:00 CST · consecutive fails: 3 · last attempts: 2
  - → fetcher 抓 www.reddit.com/r/X/top.rss（官方自助 API 已關閉、old.reddit 列表頁亦已封鎖）。若回 403 多半是雲端 / VPN 出口 IP 被擋，關掉 VPN 後跑 `uv run python -m social_info --retry-failures` 補抓；若回 429 為 RSS 節流，下次排程會依退避序列重試。
- **reddit_openai** (persistent_error) — HTTPStatusError: Client error '404 Not Found' for url 'https://www.reddit.com/r/OpenAI/top.rss?t=day&limit=10' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404
  - last ok: 2026-11-17 04:00 CST · consecutive fails: 3 · last attempts: 1
  - → fetcher 抓 www.reddit.com/r/X/top.rss（官方自助 API 已關閉、old.reddit 列表頁亦已封鎖）。若回 403 多半是雲端 / VPN 出口 IP 被擋，關掉 VPN 後跑 `uv run python -m social_info --retry-failures` 補抓；若回 429 為 RSS 節流，下次排程會依退避序列重試。
- **reddit_singularity** (persistent_error) — HTTPStatusError: Client error '404 Not Found' for url 'https://www.reddit.com/r/singularity/top.rss?t=day&limit=10' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404
  - last ok: 2026-11-17 04:00 CST · consecutive fails: 3 · last attempts: 1
  - → fetcher 抓 www.reddit.com/r/X/top.rss（官方自助 API 已關閉、old.reddit 列表頁亦已封鎖）。若回 403 多半是雲端 / VPN 出口 IP 被擋，關掉 VPN 後跑 `uv run python -m social_info --retry-failures` 補抓；若回 429 為 RSS 節流，下次排程會依退避序列重試。
- **reddit_machinelearning** (persistent_error) — HTTPStatusError: Client error '404 Not Found' for url 'https://www.reddit.com/r/MachineLearning/top.rss?t=day&limit=10' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404
  - last ok: 2026-11-17 04:00 CST · consecutive fails: 3 · last attempts: 1
  - → fetcher 抓 www.reddit.com/r/X/top.rss（官方自助 API 已關閉、old.reddit 列表頁亦已封鎖）。若回 403 多半是雲端 / VPN 出口 IP 被擋，關掉 VPN 後跑 `uv run python -m social_info --retry-failures` 補抓；若回 429 為 RSS 節流，下次排程會依退避序列重試。

