# Known Issues — auto-updated by social_info pipeline

> Last updated: 2026-09-02 08:38 (Asia/Taipei)
> 來源 source 上次 fetch 失敗的最終狀態。pipeline 已自動 retry transient errors，出現在這裡的代表 retry 配額耗盡或屬於需要人介入的類別。

## 🛠 Persistent error — fetcher 需要更新 (3)

- **twitter_tier1** (persistent_error) — RuntimeError: APIFY_TOKEN_TWITTER env var not set
  - last ok: 2026-09-01 06:00 CST · consecutive fails: 1 · last attempts: 1
  - → X 兩種失敗看 error 文字分辨：(1)「0 usable tweets / mock_tweet padding」= Apify actor 那次搜尋沒撈到任何推文，回的全是 KaitoEasyAPI 為湊最低收費塞的假資料（fetcher 已濾掉）。多半是上游 X 搜尋被限流、通常隔天自癒；若連兩天出現，拿 external-feeds 的 follow-builders X feed 對照確認推文其實存在，再考慮換 actor。(2) ReadError / timeout = 偶發網路問題，retry 即可。注意此 actor 每次呼叫都有最低消費，不要盲目重跑。
- **twitter_anthropic** (persistent_error) — RuntimeError: APIFY_TOKEN_TWITTER env var not set
  - last ok: 2026-09-01 06:00 CST · consecutive fails: 1 · last attempts: 1
  - → X 兩種失敗看 error 文字分辨：(1)「0 usable tweets / mock_tweet padding」= Apify actor 那次搜尋沒撈到任何推文，回的全是 KaitoEasyAPI 為湊最低收費塞的假資料（fetcher 已濾掉）。多半是上游 X 搜尋被限流、通常隔天自癒；若連兩天出現，拿 external-feeds 的 follow-builders X feed 對照確認推文其實存在，再考慮換 actor。(2) ReadError / timeout = 偶發網路問題，retry 即可。注意此 actor 每次呼叫都有最低消費，不要盲目重跑。
- **twitter_matt** (persistent_error) — RuntimeError: APIFY_TOKEN_TWITTER env var not set
  - last ok: 2026-09-01 06:00 CST · consecutive fails: 1 · last attempts: 1
  - → 4xx 持續錯誤 — 多半是 source schema / API 變更，需要你介入更新 fetcher。

## ⏳ Transient — retry 用完仍失敗、下次 run 會再試 (1)

- **zhihu_hot** (transient) — HTTPStatusError: Server error '503 Service Unavailable' for url 'https://rsshub.rssforever.com/zhihu/hot'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503
  - last ok: 2026-08-31 06:00 CST · consecutive fails: 2 · last attempts: 4
  - → 暫時性錯誤 — 自動 retry 用完仍失敗，下次 run 會再試。

