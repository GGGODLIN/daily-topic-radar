# 001 — 資料來源可行性與成本（無人值守、小時級輪詢）

- 研究日期：2026-08-17
- 對應票：`.scratch/deal-watch/wayfinder/tickets/001-data-source-feasibility.md`
- 場景：<10 條盯梢主題，每小時查一次有無新討論；不得佔用使用者登入的 Chrome
- 證據標記：**【實測】**＝本次自己跑過並貼出輸出；**【文件】**＝只有第三方文件或既有紀錄宣稱，本次沒跑；**【未驗】**＝引用舊結論、本次沒重驗

一句話結論：**Cursor forum 與 Reddit 完全免費且小時級綽綽有餘，X 每月約 3 美元可行，Threads 訊號最好但成本結構讓小時級直接出局、只能當觸發後的補查。**

---

## 總表

| 來源 | 路線 | 月成本 | 可靠度 | Verdict |
|---|---|---|---|---|
| Cursor forum | Discourse `/posts.json` 全站最新帖 + `/search.json` 補查 | $0 | 高 | 採用（主力） |
| Reddit | 匿名 `search.rss` + OR 合併查詢 | $0 | 中高 | 採用（主力） |
| X / Twitter | Apify `kaitoeasyapi~twitter-x-data-tweet-scraper-pay-per-result-cheapest`，關鍵字 + `since:`/`until:` | ~$2.70–3.24 | 中高 | 採用（唯一付費項） |
| Threads | Apify `watcher.data/search-threads-by-keywords` | 小時級 ~$662 → 出局；觸發式 ~$1.84 | 中 | 小時級出局，改觸發式補查 |

小時級常態總成本 ≈ **$3.2–3.7 / 月**，全部落在既有 Apify 免費額度內。

---

## 1. Cursor Community Forum（forum.cursor.com，Discourse）

### 路線 A（推薦）：`/posts.json` 全站最新帖 firehose

```
GET https://forum.cursor.com/posts.json
```

**【實測】** 匿名 curl、無 token、HTTP 200、64,849 bytes。回 `latest_posts` 陣列 50 筆，欄位含 `created_at` / `cooked`（貼文 HTML 全文）/ `topic_id` / `topic_title` / `topic_slug` / `username`。

**【實測】覆蓋率量測**：這 50 筆的時間跨度是 `2026-08-16T20:32:34.825Z` → `2026-08-17T06:38:05.808Z`，即 **10.09 小時、約 5 篇/小時**。也就是說每小時打一次 `/posts.json`，單次就涵蓋約 10 小時的回溯量，**約 10 倍安全邊際**——就算連續漏跑 9 小時也不會掉資料。關鍵字比對在本地做即可，不依賴 Discourse 的搜尋索引。

### 路線 B（補查）：`/search.json`

```
GET https://forum.cursor.com/search.json?q=<關鍵字>%20after%3A2026-08-14%20order%3Alatest
```

**【實測】** HTTP 200、回 `posts` / `topics` / `users` / `categories` / `grouped_search_result`。回應 header 含 `x-discourse-route: search/show`，確認是真的 Discourse 應用層回的，不是 Cloudflare 快取頁。

**【實測】搜尋運算子**：
- `q=discount` → 50 posts
- `q=discount after:2026-08-14` → 1 post（`after:` 有效）
- `q=after:2026-08-14` → 50 posts
- `q="limited time" order:latest` → 24 posts / 24 topics
- **【實測】** `q=discount after:2026-08-01` 直接撈到官方員工 deanrie 2026-08-14 的回覆，說明 P2P referral / invite-code 方案已於 2026-07-23 結束、官方折扣只剩 student discount 與首次訂閱 promo。這正是 A 類「有沒有人找到門路」要的內容。

### Rate limit

**【實測】** 連打 12 發 `/search.json?page=N`（無間隔）：`200 200 200 200 429 200 200 429 429 200 400 400`。→ 匿名搜尋約 5 發連打就會撞 429；page 11 以後回 400（搜尋分頁上限）。

小時級只需 1–2 發，完全不受影響。

### 成本

$0。無 token、無帳號、無 quota。

### 可靠度

**高**。Discourse 公開 JSON API 是官方功能而非灰色爬蟲，`/posts.json` 是 Discourse 內建路由（不是 Cursor 自訂），升級不太會壞。Cloudflare 在前面但對 plain curl 沒有出 challenge。

**未驗風險**：只做了單一時點探測，沒有觀察多日穩定性；論壇若哪天關閉匿名 API（Discourse 有 `login_required` 設定）整條路會一起死。

---

## 2. Reddit

### 路線：匿名 `search.rss` + OR 合併查詢

```
GET https://www.reddit.com/search.rss?q=<urlencoded OR query>&sort=new&limit=25
User-Agent: <自訂識別字串>
```

**【實測】命中靶心**。查詢：

```
(supergrok OR "cursor ultra" OR "claude max") (discount OR promo OR coupon OR "off")
```

HTTP 200、107,251 bytes、25 個 `<entry>`。其中一筆是：

> `r/malaysiadiscount` — 「How to trigger SuperGrok Heavy $99 promo for 3 months on any acco…」，`updated` = 2026-08-16T23:42:34

這就是 A 類「已知優惠、盯有沒有人找到門路」的正命中，不是理論可行性。

**設計要點**：Reddit search 支援布林運算子，**所有盯梢詞可以合併成一發請求**，因此小時級只需 1 request/hour，而不是 10 requests/hour。這同時解決了下面的 rate limit 問題。

### 【實測】索引延遲 = 1–2 分鐘

查詢時間 06:51:20 UTC，`q=cursor OR claude OR grok&sort=new` 回來最新 5 筆：

| lag | 貼文時間 | 標題 |
|---|---|---|
| 1.0 min | 06:50:20 | Your AI finally understands your business |
| 1.4 min | 06:50:00 | Grok evaluation of enforced watermarking… |
| 1.4 min | 06:49:56 | Day 30 of giving two Claude agents €100… |
| 1.9 min | 06:49:28 | Part 3: TelemetryDeck Summer Update 2026 |
| 2.0 min | 06:49:23 | Your AI finally understands your business |

對小時級 SLA 來說索引延遲可忽略。

### Rate limit（唯一真風險）

**【實測】** 本次量到兩次 429：
- 間隔 5 秒的第二發 → HTTP 429、size 0
- 間隔 20 秒的第二發 → HTTP 429、size 0
- 間隔 45 秒以上 → 200

跟 repo 內既有實作對得上：`src/social_info/fetchers/reddit.py` 檔頭與常數 `MIN_REQUEST_GAP_SECONDS = 45.0`、`THROTTLED_RETRY_DELAY_SECONDS = 90.0`，並用 module-level lock 串行化所有 reddit 請求。

**含義**：新的小時級 poller **不能跟既有每日 digest 的 reddit fetch 同時跑**。既有 pipeline 一次串 5 個 subreddit、每發間隔 45 秒 ≈ 佔用 4 分鐘。排程要錯開，或共用同一把 lock。

### 已死的路（本次實測確認）

- **【實測】** `https://www.reddit.com/r/cursor/new.json?limit=3` → **HTTP 403**（回 189,908 bytes 的 HTML 攔截頁）。匿名 `.json` 確定不能用。
- **【實測】** `https://www.reddit.com/r/cursor/search.rss?...`（subreddit-scoped）→ 兩次嘗試都 429，**從未拿到乾淨 200**。不能宣稱它壞了，只能說**本次未驗證可用**；要用 subreddit-scoped 搜尋前必須先單獨補測。
- **【文件】** `old.reddit.com/r/{sub}/top/` 自 2026-08-11 起對未登入請求 302 導向 `/login?reason=lor2`，httpx 跟隨轉址後回 200 登入頁、解析出 0 篇——來源會**靜默消失**而不進 failures。出處：`src/social_info/fetchers/reddit.py` 檔頭 docstring。
- **【未驗】** 官方 Data API：2025-11-11 起關閉自助申請、改人工審核，indie dev 拿不到。出處：`file:///Users/linhancheng/.claude/memory/reference_reddit_api_2025_11_policy_change.md`（110 天前的紀錄，**本次沒有重驗**）。因為免費路線已足夠，不建議為此花時間。

### 成本

$0。

### 可靠度

**中高**。免費、近即時、命中率已實證；扣分項是 Reddit 對匿名流量的節流很兇且無預警，以及 `search.rss` 的結果會混進 subreddit / community 本身（本次見到 `updated` 為 2012-03-10、2024-01-13 的條目），**必須用 `updated` 時間戳在本地過濾**，不能直接把回傳當新貼文。

---

## 3. X / Twitter

### 發現（discovery）路線：Apify Tweet Scraper，關鍵字模式

Actor：`kaitoeasyapi~twitter-x-data-tweet-scraper-pay-per-result-cheapest`
Endpoint：`POST https://api.apify.com/v2/acts/<actor>/run-sync-get-dataset-items?token=$APIFY_TOKEN_TWITTER`

**本次最重要的發現**：既有 repo 只把這個 actor 當「抓 handle timeline」用（`src/social_info/fetchers/twitter.py` 組 `from:{handle} since:… until:…`），但 **`searchTerms` 吃的是 X 原生搜尋語法，任意關鍵字查詢直接可用**。不需要換 actor、不需要新帳號。

**【實測】** payload `{"searchTerms":["SuperGrok Heavy discount","cursor pro discount"],"maxItems":6,"queryType":"Latest"}` → HTTP 201、24.6 秒、40 筆真 tweet，全部命中主題，最新一筆是查詢當下 1 分鐘前：

> `@GrokInsider` 2026-08-17 06:37:15 UTC — 「SuperGrok Heavy ANNUAL for $999 / ONLY works for those who already got SuperGrok Heavy 67% discount. / I doubt this one will last longer than 1 hour guys.」

**【實測】時間窗有效**：`since:`/`until:` 運算子在 `searchTerms` 內可用，實測 1 小時窗與 24 小時窗都正確生效，這是控制成本的主要槓桿。

### 【實測】計價與最低收費（成本模型核心）

計價 $0.25 / 1,000 results = $0.00025/result。三次實跑對帳（`GET /v2/acts/<actor>/runs` 的 `usageTotalUsd`）：

| 測試 | 回傳筆數 | 實際計費 | 對帳 |
|---|---|---|---|
| 2 關鍵字、無時間窗 | 40 真 tweet | $0.01000 | 40 × $0.00025 ✓ |
| 5 關鍵字、24h 窗 | 180 真 tweet | $0.04500 | 180 × $0.00025 ✓ |
| 1 關鍵字、1h 窗、零命中 | 15 筆 `mock_tweet` | $0.00375 | 15 × $0.00025 ✓ |
| 3 關鍵字、1h 窗、零命中 | 15 筆 `mock_tweet` | $0.00375 | 同上 |

**關鍵結論**：查無結果時 actor 會塞 15 筆 `mock_tweet`（內容是「From KaitoEasyAPI, a reminder: Our API pricing is based on the volume o…」）並照收費。**這 15 筆與 searchTerms 數量無關**（1 個詞和 3 個詞都是 15 筆），所以每次呼叫有 **$0.00375 的固定地板價**。

repo 已知道這個行為並有防呆：sources.yml 註解「查無結果時會回 mock_tweet 湊數；fetcher 濾掉 mock，若濾完為空會 raise 進 KNOWN_ISSUES（2026-07-31 靜默漏抓一整天後補的偵測）」。

**`maxItems` 不是可靠的成本上限**：實測 `maxItems: 6` 回了 40 筆並照 40 筆收費。真正的量控槓桿是 `since:`/`until:` 時間窗。

### 月成本估算

**【實測】真實流量基準**：5 個優惠類關鍵字、24 小時窗 → 180 則真 tweet = **7.5 則/小時**（且今天是 SuperGrok 優惠正熱的日子，屬偏高估）。

每小時單次呼叫（所有盯梢詞合併進同一個 `searchTerms` 陣列 = 一次 run）：

```
每小時實際命中成本 = 7.5 × $0.00025 = $0.0019  <  地板價 $0.00375
→ 地板價主導

月成本 = 24 × 30 × $0.00375 = $2.70
```

加上部分時段爆量的加成（假設 20% 的小時命中 30 則，超出地板部分 = 144 × $0.00375 = $0.54）：

```
X 小時級月成本 ≈ $2.70 ~ $3.24
```

**【實測】共用額度提醒**：這筆錢跟既有每日 digest 共用同一個 Apify 免費帳號。既有 pipeline 實測日成本 $0.0155（2026-08-16 全天，62 results）→ **$0.47/月**。帳號 `GGGODLIN`、plan `FREE`、`maxMonthlyUsageUsd: 5`、本週期（2026-07-26 ~ 08-25）已用 **$0.7927**。

```
合計 ≈ $3.2 ~ $3.7 / 月  vs  $5 免費額度 → 剩約 26–36% headroom
```

降頻到兩小時一次可砍半至 $1.35/月，headroom 拉到 70%+。

### 補文（enrichment）路線：免費

**【實測】** `bash ~/.claude/scripts/x-fetch.sh <tweet_url>` → exit 0，走 tier1 syndication CDN（8,073b）+ tier2 fxtwitter v1（8,596b），拿到完整貼文全文，包含上面那則 GrokInsider 的完整五步驟操作教學。**免帳號、免 API key、headless、不碰使用者 Chrome。**

**【實測】舊況重驗**：`WebFetch` 對 `https://x.com/.../status/...` **仍然回 HTTP 402 Payment Required**（2026-08-17 實測）。票上要求重驗的這一點結論不變，但**已不構成阻礙**——x-fetch.sh 是既有且有效的替代路徑。

### 可靠度

**中高**。actor 本身 2026-07-31 有過一次靜默漏抓（已有偵測補上）。**【未驗】** actor 頁面自稱的 uptime / 評分數字在 2026-06-03 的 deep-research 中被判定為行銷文案而非平台計算指標，不採信（出處：`file:///Users/linhancheng/.claude/memory/projects/social-info/reference_threads_apify_scrapers.md` 第 110 行）。

**未驗風險**：只測了「全部命中」與「全部落空」兩種情況，**沒測部分命中時是否仍補 mock 列**——若補，成本會比估算高。上線後應該用 `usageTotalUsd` 對帳前兩週。

---

## 4. Threads

### 【實測】重要更正：既有紀錄與 repo 設定都過期了

`sources.yml` 目前寫：

> `threads_keyword` — `enabled: false`
> 「Disabled 2026-05-08: Apify actor D15iJFBNZ9wgeWAhw 持續 400 (payload schema)。8 天連續失敗，retry 救不回。」

**2026-08-17 重測：這個 actor 現在是好的。** payload `{"keywords":["SuperGrok","Cursor"],"maxItemsPerKeyword":3,"sortByRecent":true}` → **HTTP 201、15.0 秒、37 筆真資料**。2026-05-08 的停用理由已失效。

### 【實測】訊號品質是四個來源裡最高的

`watcher.data/search-threads-by-keywords`（actor ID `D15iJFBNZ9wgeWAhw`）做的是真正的片語精準比對，兩次查詢都直接命中本次的靶心主題：

- `["SuperGrok","Cursor"]` → `@dingyi`：「昨晚用新账号抢到了 $99/月的 SuperGrok Heavy，于是我拥有了两个 Cursor Ultra…」
- `["SuperGrok Heavy"]` → `@alexgetmanru`：「🚨 SuperGrok Heavy можно забрать за $99 на 3 месяца / 1. Подключаете SuperGrok за…」（俄文的完整操作步驟）

**這是 X 和 Reddit 抓不到的層**：中文圈與俄文圈的實作教學集中在 Threads。訊號價值最高。

### 【實測】成本結構讓小時級直接出局

計價（查 `GET /v2/acts/D15iJFBNZ9wgeWAhw`）：`pricingModel: PAY_PER_EVENT`、`apify-default-dataset-item` = **$0.008/result**（另有 2025-08-27 起的舊版 `PRICE_PER_DATASET_ITEM` 同樣 `pricePerUnitUsd: 0.008`）。與既有紀錄的 $8/1000 一致，**本次重驗仍然有效**。

**致命點：`maxItemsPerKeyword` 完全不限制計費。**

| 請求 | 實際回傳 | 實際計費 |
|---|---|---|
| 1 關鍵字、`maxItemsPerKeyword: 1` | **23 筆** | **$0.18400** |
| 2 關鍵字、`maxItemsPerKeyword: 3`（= 期望 6 筆） | **37 筆** | **$0.29600** |

→ 每個關鍵字每次呼叫實質固定收 18–23 筆 ≈ **$0.184 地板價**，且沒有時間窗參數可以壓（input schema 只有 `keywords` / `maxItemsPerKeyword` / `sortByRecent` / `outputFormat` / `proxyConfiguration`，**沒有 dateFrom / dateTo**）。

月成本推算：

```
小時級 × 5 關鍵字 = 24 × 30 × 5 × $0.184 = $662 / 月     ← 超出 $5 額度約 132 倍
每日一次 × 5 關鍵字 = 30 × 5 × $0.184   = $27.6 / 月    ← 仍超出 5.5 倍
$5 免費額度可負擔       ≈ 27 次「關鍵字 × 呼叫」/ 月
```

帳號 `boisterous_xystos`、plan `FREE`、`maxMonthlyUsageUsd: 5`、本週期已用 $0.7853。

### 【實測】四個便宜替代 actor 全部因相關度不合格而淘汰

從 Apify store API（`GET /v2/store?search=threads&limit=50`）撈出 45 個 actor，挑出有 keyword search 能力且更便宜的逐一實測：

| Actor | 單價 | 採用度 | 實測結果 |
|---|---|---|---|
| `parsebird/threads-scraper` | $0.0001 | 17 users / 219 runs 30d | **相關度不合格**。`max_posts: 5` 有確實生效（回正好 5 筆、成本可控），但「SuperGrok Heavy」只比對到 `heavy` 單字：Daily Heavy Quotes 語錄、足球 heavy press、重型卡車模型、heavy weekend 健身文。零相關。 |
| `sleek_waveform/threads-scraper` | $0.00001 | 77 users / 233 runs 30d | **相關度不合格**。「SuperGrok Heavy」→ Superman / 孫悟空 / 七龍珠討論。 |
| `constructive_calm/threads-search-monitor` | $0.0015 | 29 users / 1946 runs 30d | **相關度不合格**。名稱標榜 keyword tracking、input 有 `dateFrom`/`dateTo`/`maxItems` 看起來完美，但「SuperGrok」→「Süper süper 🤣」「Who's the worst Superman?」「Cari seller super grok」。 |
| `vitalue/threads-scraper` | $0.00005 | 101 users / 3433 runs 30d | **回 0 筆**。HTTP 201 但空陣列。 |

結論：**Threads 上唯一做得到片語精準比對的就是 $0.008 的 watcher.data**，便宜貨全部是單字模糊比對。價差 80–800 倍換來的是完全不能用的結果。這不是「省錢 vs 品質」的取捨，是「能用 vs 不能用」。

（`watcher.data` 採用度反而是這批裡最高的：1,759 users / 17,087 runs 30d。與 2026-06-03 deep-research「Threads actor 採用度普遍很弱」的印象相比，這支現在算成熟。）

### 【實測】免費路線全部死路

- `https://www.threads.com/search?q=SuperGrok` → HTTP 200 但 body 是登入牆（HTML 內含 login）
- `https://rsshub.app/threads/zuck` → **HTTP 403**
- **【未驗】** RSSHub 官方 Threads route 只有 user 模式、無 keyword search（出處：`file:///Users/linhancheng/.claude/memory/projects/social-info/reference_threads_apify_scrapers.md`，本次只驗到 403 就停，沒有再查 route 清單）

### 【實測】補文是免費的

`bash ~/.claude/scripts/fetch-fallback.sh 'https://www.threads.com/@dingyi/post/DcIV5l5m6pa'` → **exit 0**，走 general track 的 Googlebot UA 成功取得 HTML。

→ 跟 X 一樣的結構：**發現（search）要錢，補文（單篇 URL）免費。**

### Verdict

**小時級發現：出局**（成本超出 132 倍，且沒有任何參數能壓低單次呼叫量）。

**觸發式補查：採用**。當 X 或 Reddit 的小時級迴圈標記出一個候選優惠時，對該單一關鍵字打一次 watcher.data（$0.184），把中文／俄文圈的操作教學撈回來。以每月 10 次觸發估：

```
10 × $0.184 = $1.84 / 月   ← 落在 $5 免費額度內
```

---

## 5. 推薦組合

**小時級常態迴圈（三個來源、一次呼叫各一發）**：Cursor forum 走 `/posts.json` 拉全站最新 50 帖在本地比對關鍵字（$0，單次涵蓋約 10 小時、10 倍安全邊際）；Reddit 走匿名 `search.rss`，把全部盯梢詞用布林 OR 合併成單一 query（$0，索引延遲實測 1–2 分鐘，合併查詢同時規避了 45 秒節流限制）；X 走既有的 Apify actor，同樣把全部盯梢詞塞進同一個 `searchTerms` 陣列並帶 `since:`/`until:` 一小時窗（實測地板價 $0.00375/次 → 約 $2.70–3.24/月）。三者合計 **$3.2–3.7/月**，全部落在既有 `GGGODLIN` Apify 免費帳號的 $5 額度內（本週期已用 $0.79，另需分攤既有每日 digest 的 $0.47/月）。Threads **不進小時級迴圈**——它訊號最好但單次呼叫地板價 $0.184 且無法壓低，改成觸發式：迴圈標到候選優惠時才打一次單關鍵字（約 $1.84/月 @ 10 次觸發），專門撈 X／Reddit 覆蓋不到的中文與俄文操作教學。命中後的補文全部免費且 headless：X 用 `x-fetch.sh`、Threads 與一般 URL 用 `fetch-fallback.sh`、Discourse 直接吃 `/posts.json` 回傳的 `cooked` 欄位——**四條路沒有任何一條需要碰使用者登入的 Chrome**，硬約束滿足。若要再省，把 X 降到兩小時一次即可砍到 $1.35/月，代價是最壞情況延遲從 1 小時變 2 小時。

### 上線前必須先補的驗證

1. **X actor 部分命中時是否仍補 mock 列** — 本次只測了全命中與全落空兩極，中間態未知，會影響成本估算。上線後兩週用 `usageTotalUsd` 對帳。
2. **Reddit 排程互斥** — 新 poller 不能與既有每日 digest 的 reddit fetch 撞在一起（後者串行佔用約 4 分鐘）。
3. **subreddit-scoped `search.rss`** — 本次兩次嘗試都 429、從未拿到乾淨 200。若設計需要限定 subreddit，必須先單獨補測。
4. **`threads_keyword` 重啟** — sources.yml 那條 `enabled: false` 的停用理由已經過期（actor 實測正常），但**不建議直接改成 true**：每日 17 個關鍵字 × $0.184 ≈ $94/月，會瞬間燒穿免費額度。要重啟必須先改成觸發式或大幅砍關鍵字數。

### 什麼證據會推翻上述結論

- X actor 的 `mock_tweet` 最低收費若改成隨 searchTerms 數量放大 → X 月成本從 $2.70 變成隨盯梢詞數線性成長，可能超出免費額度
- Discourse 若開啟 `login_required` 或關閉匿名 API → 路線 A 與 B 同時死，該來源需重新評估
- Reddit 若把 `search.rss` 一併關掉（`.json` 已 403、`old.reddit` 已 302）→ 只剩 arctic-shift 歸檔（有 snapshot 延遲，不適合小時級）
- 出現任何一個**片語精準比對**的便宜 Threads actor → Threads 可以升回小時級迴圈
- 本研究全部是 2026-08-17 單一時點的探測，**沒有任何多日穩定性證據**；任一來源連續數日失敗都會推翻其可靠度評級

---

## 附錄：本次實測用到的端點與檔案

**外部端點**
- https://forum.cursor.com/posts.json
- https://forum.cursor.com/search.json
- https://www.reddit.com/search.rss
- https://api.apify.com/v2/acts/kaitoeasyapi~twitter-x-data-tweet-scraper-pay-per-result-cheapest/run-sync-get-dataset-items
- https://api.apify.com/v2/acts/D15iJFBNZ9wgeWAhw/run-sync-get-dataset-items
- https://api.apify.com/v2/store?search=threads
- https://apify.com/watcher.data/search-threads-by-keywords

**本機檔案**
- `sources.yml`（既有 twitter / threads source 設定與註解）
- `src/social_info/fetchers/twitter.py`（Apify actor 呼叫方式、$0.25/1K 計價註解）
- `src/social_info/fetchers/reddit.py`（`top.rss` 路線、45 秒節流、old.reddit 302 記載）
- `file:///Users/linhancheng/.claude/scripts/x-fetch.sh`
- `file:///Users/linhancheng/.claude/scripts/fetch-fallback.sh`
- `file:///Users/linhancheng/.claude/memory/projects/social-info/reference_threads_apify_scrapers.md`
- `file:///Users/linhancheng/.claude/memory/projects/social-info/reference_x_tweet_fetch_fallback.md`
- `file:///Users/linhancheng/.claude/memory/reference_reddit_api_2025_11_policy_change.md`
- `file:///Users/linhancheng/.claude/memory/reference_arctic_shift_reddit_api.md`

**本次研究產生的 Apify 花費**（逐 run 的 `usageTotalUsd` 加總，非帳號前後差值）：

- TWITTER 帳號：$0.01000 + $0.04500 + $0.00375 + $0.00375 = **$0.0625**。研究結束時月累計 $0.7927 / $5。
- THREADS 帳號：watcher.data 兩次 $0.29600 + $0.18400 = **$0.48**，另加四個便宜 actor 的測試（單價 $0.00001–$0.0015 × 5 筆，合計低於 $0.01、未逐筆對帳）。研究結束時月累計 $0.7853 / $5。

兩個帳號都仍在免費額度內。註：THREADS 帳號在本次研究時段內另有兩筆 06:22 / 06:26 的 watcher.data run（$0.128 + $0.144），**不確定是否由本次研究觸發**，故未計入上面加總；月累計數字已包含它們。
