# Backlog

> 想到但尚未做的 idea。不是 spec，只是備忘。

## 下個階段：拓展為多主題 framework

**現況**：repo 名 `daily-topic-radar` 已暗示通用化、但實作層面仍是 AI 為主——`sources.yml` 的 enabled source、HN keyword filter、X KOL 名單、BlockTempo `/category/ai/feed/` URL、文件範例都圍繞 AI 寫死。

**想要**：能讓同一個 framework 跑多個主題（AI、Shopify 商家動態、競品追蹤、個人投資組合、興趣領域），互不干擾。

**架構面要動的地方**（草擬，未細化）：

1. **Topic 概念進入 config**：
   - 每個主題自己的 `sources/<topic>.yml`、自己的 `state-<topic>.db`、自己的 `reports/<topic>/YYYY-MM-DD.md`
   - CLI 加 `--topic <name>` flag、預設讀 `sources.yml` 視為 `default` topic（向後相容）
   - GitHub Actions 改成 matrix run（每個 topic 平行跑）

2. **Keyword filter 從 hardcoded 變成 config-driven**：
   - 目前 HN fetcher 內 keywords 是 source params 的一部分，已經 config-driven
   - 但 GitHub Trending 的 ai_keywords、Threads keyword search queries 也都已經在 yml 內，這部分其實已經 OK
   - 主要是把 KOL 清單、Reddit subreddit list、RSS URL 列表都按 topic 分組

3. **Source 分類機制**：
   - 有些 source 跨主題通用（HN、GitHub Trending），有些主題專屬（X KOL、特定 RSS）
   - 可能需要「source library」+「topic recipe」分離：source library 定義可用 source、topic recipe 從 library 挑選 + 客製化參數

4. **Markdown 渲染按 topic 客製分組**：
   - 目前 `PLATFORM_GROUP_ORDER` 寫死在 `markdown.py`
   - 通用化：分組規則進 topic config

5. **README + spec 重寫成 topic-agnostic**：
   - 把 AI 例子降成「default topic 範例」
   - spec 拆「framework spec」+「AI topic recipe spec」

**Why 不現在做**：
- 第一個主題（AI）還沒驗證滿一個月
- 過早抽象 = YAGNI
- 等實際多日跑下來看 stage-2 工作流哪裡需要調，再順便重構

**何時動**：跑滿 4 週、確認 AI topic 工作流順、且第二個 topic（例如 Shopify 商家動態 / 個人興趣某領域）有具體想法時，做一次 refactor。

---

## X / Twitter source 的 fallback：Chrome extension pattern

**現況**：X source 走 Apify Tweet Scraper Actor，月 $5 free credits 自動更新、$0/月。

**備案觸發**：當 Apify 出現以下任一情況時，啟用備案：
- Apify 政策變、$5 monthly credits 取消或縮水
- Apify 上 Tweet Scraper Actor 全部失效 / 不再維護
- Apify 平台被 X 持續 block（會看後續穩定度）
- 使用者想完全 own 整條 pipeline 不依賴第三方

**備案內容**：fork [`~/Desktop/projects/cc-quota-fetcher`](file:///Users/linhancheng/Desktop/projects/cc-quota-fetcher) 改成 `x-feed-fetcher`：
- Chrome extension 在使用者 X 帳號 session 內 fetch X 內部 GraphQL endpoint
- Native messaging host 寫 `~/.cache/x-feed/<handle>.json`
- `social_info/fetchers/twitter.py` 改成讀本機 cache file（不再打外部 API）
- daily.yml cron 改成本機 macOS launchd（雲端 runner 讀不到本機 cache）
- 工程量約 4-7 hr

**為什麼不現在做**：Apify 月 $0、零工程、雲端跑——比 extension 路徑便宜且簡單。等 Apify 真的出問題再切換。

**詳細 pattern reference**：見 `cc-quota-fetcher` repo README，含「為什麼 cookie extract 路線走不通」「extension 內 fetch 為什麼通」的完整論述。
