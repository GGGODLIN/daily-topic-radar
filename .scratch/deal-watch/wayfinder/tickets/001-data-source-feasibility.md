---
status: closed
type: research
blocked-by: []
---

## Question

無人值守、小時級輪詢 Threads／X／Cursor forum／Reddit 等社群來源，可行的抓取路線與成本各是什麼？約束：不能佔用使用者登入的 Chrome（claude-in-chrome 路線出局）；Threads 有 2026-06 的 Apify scraper 評估需更新（memory `reference_threads_apify_scrapers`、`_index_web_fetch` cluster）；X 的 WebFetch 402 舊況需重驗。產出：每個來源一行「路線／月成本估算／可靠度／出局理由」，附引用。

## Resolution

詳見 [research/001-data-sources.md](../research/001-data-sources.md)（2026-08-17，含實測與推翻條件）。結論：
- **Cursor forum**：採用（主力）。Discourse `/posts.json` 匿名實測 200、約 5 帖/時、$0。
- **Reddit**：採用（主力）。匿名 `search.rss` 布林 OR 合併盯梢詞單發、實測索引延遲 1–2 分鐘、$0；風險＝429（需 ≥45 秒間隔、與既有 daily reddit fetch 錯開）。
- **X**：採用（唯一付費）。沿用既有 Apify actor 的 `searchTerms`（原生搜尋語法＋since/until 直接可用）；零命中地板價 $0.00375/次 → 小時級約 $2.7–3.2/月，在既有 $5 免費額度內。
- **Threads**：小時級出局（計費不受 maxItems 限制、5 詞≈$662/月；便宜替代 actor 全是單字模糊比對不可用），降為觸發式補查（$0.184/次）；注意 sources.yml 舊註記「actor 400」已過期、今日實測 201 且訊號最好。
- 推薦組合：三來源小時級合計 $3.2–3.7/月＋Threads 觸發式 ≈$1.8/月。上線前待補驗四項（列在報告末兩節）。