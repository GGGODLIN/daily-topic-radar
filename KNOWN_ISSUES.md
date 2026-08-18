# Known Issues — auto-updated by social_info pipeline

> Last updated: 2026-08-18 07:45 (Asia/Taipei)
> 來源 source 上次 fetch 失敗的最終狀態。pipeline 已自動 retry transient errors，出現在這裡的代表 retry 配額耗盡或屬於需要人介入的類別。

## ⏳ Transient — retry 用完仍失敗、下次 run 會再試 (2)

- **twitter_tier1** (transient) — ReadError: 
  - last ok: 2026-08-17 06:00 CST · consecutive fails: 1 · last attempts: 4
  - → X 兩種失敗看 error 文字分辨：(1)「0 usable tweets / mock_tweet padding」= Apify actor 那次搜尋沒撈到任何推文，回的全是 KaitoEasyAPI 為湊最低收費塞的假資料（fetcher 已濾掉）。多半是上游 X 搜尋被限流、通常隔天自癒；若連兩天出現，拿 external-feeds 的 follow-builders X feed 對照確認推文其實存在，再考慮換 actor。(2) ReadError / timeout = 偶發網路問題，retry 即可。注意此 actor 每次呼叫都有最低消費，不要盲目重跑。
- **twitter_anthropic** (transient) — ReadError: 
  - last ok: 2026-08-17 06:00 CST · consecutive fails: 1 · last attempts: 4
  - → X 兩種失敗看 error 文字分辨：(1)「0 usable tweets / mock_tweet padding」= Apify actor 那次搜尋沒撈到任何推文，回的全是 KaitoEasyAPI 為湊最低收費塞的假資料（fetcher 已濾掉）。多半是上游 X 搜尋被限流、通常隔天自癒；若連兩天出現，拿 external-feeds 的 follow-builders X feed 對照確認推文其實存在，再考慮換 actor。(2) ReadError / timeout = 偶發網路問題，retry 即可。注意此 actor 每次呼叫都有最低消費，不要盲目重跑。

