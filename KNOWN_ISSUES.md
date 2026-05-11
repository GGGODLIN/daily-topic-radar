# Known Issues — auto-updated by social_info pipeline

> Last updated: 2026-05-11 08:26 (Asia/Taipei)
> 來源 source 上次 fetch 失敗的最終狀態。pipeline 已自動 retry transient errors，出現在這裡的代表 retry 配額耗盡或屬於需要人介入的類別。

## ⏳ Transient — retry 用完仍失敗、下次 run 會再試 (1)

- **twitter_tier1** (transient) — ReadError: 
  - last ok: 2026-05-10 06:08 CST · consecutive fails: 1 · last attempts: 4
  - → X 偶發 ReadError。先試 retry，仍失敗檢查 RSS hub / scrape 設定。

