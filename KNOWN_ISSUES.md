# Known Issues — auto-updated by social_info pipeline

> Last updated: 2026-11-27 04:17 (Asia/Taipei)
> 來源 source 上次 fetch 失敗的最終狀態。pipeline 已自動 retry transient errors，出現在這裡的代表 retry 配額耗盡或屬於需要人介入的類別。

## 🛠 Persistent error — fetcher 需要更新 (4)

- **reddit_claudeai** (persistent_error) — HTTPStatusError: Client error '422 Unprocessable Entity' for url 'https://arctic-shift.photon-reddit.com/api/posts/search?subreddit=ClaudeAI&after=1795615210&limit=100&sort=desc&before=1795639461' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/422
  - last ok: 2026-11-26 04:00 CST · consecutive fails: 1 · last attempts: 1
  - → fetcher 抓 arctic-shift.photon-reddit.com/api/posts/search（Reddit 已於 2026-11-13 永久終止原生 RSS、官方公開 API 亦已關申請）。若回 429/5xx 多半是 Arctic Shift 上游限流或維護，稍後跑 `uv run python -m social_info --retry-failures` 補抓；若持續不可用需改評估 Apify 備援或停用來源。
- **reddit_openai** (persistent_error) — HTTPStatusError: Client error '422 Unprocessable Entity' for url 'https://arctic-shift.photon-reddit.com/api/posts/search?subreddit=OpenAI&after=1795615210&limit=100&sort=desc' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/422
  - last ok: 2026-11-26 04:00 CST · consecutive fails: 1 · last attempts: 1
  - → fetcher 抓 arctic-shift.photon-reddit.com/api/posts/search（Reddit 已於 2026-11-13 永久終止原生 RSS、官方公開 API 亦已關申請）。若回 429/5xx 多半是 Arctic Shift 上游限流或維護，稍後跑 `uv run python -m social_info --retry-failures` 補抓；若持續不可用需改評估 Apify 備援或停用來源。
- **reddit_singularity** (persistent_error) — HTTPStatusError: Client error '422 Unprocessable Entity' for url 'https://arctic-shift.photon-reddit.com/api/posts/search?subreddit=singularity&after=1795615210&limit=100&sort=desc' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/422
  - last ok: 2026-11-25 04:00 CST · consecutive fails: 2 · last attempts: 1
  - → fetcher 抓 arctic-shift.photon-reddit.com/api/posts/search（Reddit 已於 2026-11-13 永久終止原生 RSS、官方公開 API 亦已關申請）。若回 429/5xx 多半是 Arctic Shift 上游限流或維護，稍後跑 `uv run python -m social_info --retry-failures` 補抓；若持續不可用需改評估 Apify 備援或停用來源。
- **reddit_machinelearning** (persistent_error) — HTTPStatusError: Client error '422 Unprocessable Entity' for url 'https://arctic-shift.photon-reddit.com/api/posts/search?subreddit=MachineLearning&after=1795615210&limit=100&sort=desc' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/422
  - last ok: 2026-11-26 04:00 CST · consecutive fails: 1 · last attempts: 1
  - → fetcher 抓 arctic-shift.photon-reddit.com/api/posts/search（Reddit 已於 2026-11-13 永久終止原生 RSS、官方公開 API 亦已關申請）。若回 429/5xx 多半是 Arctic Shift 上游限流或維護，稍後跑 `uv run python -m social_info --retry-failures` 補抓；若持續不可用需改評估 Apify 備援或停用來源。

## ⏳ Transient — retry 用完仍失敗、下次 run 會再試 (1)

- **zhihu_hot** (transient) — HTTPStatusError: Server error '503 Service Unavailable' for url 'https://rsshub.rssforever.com/zhihu/hot' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503
  - last ok: 2026-11-26 04:00 CST · consecutive fails: 1 · last attempts: 4
  - → 暫時性錯誤 — 自動 retry 用完仍失敗，下次 run 會再試。

