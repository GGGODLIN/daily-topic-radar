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

---

## Threads keyword search dead-end (2026-04-26)

**現況**：Threads `keyword_search` / topic tag search 在 Meta App 處於 **Development mode** 時，**只能搜到 test user (gggodlin) 自己的 posts**。對 daily aggregator 零價值。

**根本原因**：[Meta 文件](https://developers.facebook.com/docs/threads/keyword-search/) 明確規定：
> 未獲得權限時：只會針對已驗證用戶所擁有的貼文進行搜尋。
> 獲得權限後：即可搜尋公開貼文。

「獲得權限」不等於 console 內 toggle on `threads_keyword_search`——那只是 app **request 這個 scope**。實際生效要：
1. 提交 Meta App Review
2. 提供 use case justification、privacy policy URL、ToS URL、app icon、production app domain
3. Review pass、進 Live mode

這條對個人 PoC 不友善——個人專案沒 production deployment 證明，App Review 大概不會 pass。

**已採取的處置**：
- `threads_keyword` 與 `threads_topic_tag` source 在 sources.yml 改為 `enabled: false`
- 完整 OAuth flow / app secret / long-lived access token 仍保留（GitHub Secrets 還在），未來如改走別條路可立即復用

**未來三條 path**（按推薦排）：

1. **走 Chrome extension fallback**（同 X source 走過的路）：fork `cc-quota-fetcher` 改成 `threads-feed-fetcher`，extension 在你 Threads 帳號 session 內 fetch 內部 GraphQL endpoint、寫到 `~/.cache/threads-feed/<query>.json`，pipeline 改成讀本機 file。同 X 的 architecture trade-off：要 Mac 24/7 開 + 工程量 4-7 hr。
2. **submit Meta App Review**：填一堆表格、寫 use case justification、設 privacy policy 與 ToS URL（個人 GitHub Pages 可用）。Review 結果不確定。如果通過，Threads keyword_search 就能正常用、不需 chrome extension。工程量 2-4 hr 加等待 review。
3. **接受 Threads 不可用**：35 個 source 已很完整、Threads 訊號靠中文 / 台灣其他 source（iThome、BlockTempo AI）部分覆蓋。不做就不做。

**為什麼不現在做**：35 個 source 的 daily digest 對 stage-2 工作流足夠驗證（跑兩週看訊號量是否合用）。Threads 問題是「nice-to-have signal source 缺一個」，不是「pipeline 壞了」。等 PoC 跑滿一個月、確認 Threads 真的需要時再花心力解。

**關鍵紀錄**：
- App ID: `1492688529247308` (ak-threads-booster-gggodlin)
- App Secret: 已存在 `THREADS_APP_SECRET` GitHub Secret
- Long-lived Access Token: 已存在 `THREADS_ACCESS_TOKEN` GitHub Secret，2026-06-25 過期
- OAuth redirect URI: `https://example.com/oauth/callback`（dummy、未來換 self-host server 時改）
