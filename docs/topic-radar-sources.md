# 研究選題雷達的來源與服務配置方案

## 本輪已批准的範圍

使用共用來源配置與抓取，供共用資料池、daily topic 與選題雷達取用。保存抓取端已取得的完整正文；沒有全文的來源保留摘要與資料限制，不追加抓頁請求。新雷達只讀本機資料，不呼叫新模型 API。使用者於 2026-10-14 核准新增 14 個 X 帳號，沿用現役 Apify relay、每日一次、每帳號最多 10 筆與每次 Actor 費上限 US$0.01，包含首輪實抓驗證；只消耗既有額度，不充值、不新增訂閱。

## 推薦帳號

來源是 [new-term-radar 固定版本的範例配置](https://github.com/zhuyansen/new-term-radar/blob/dd2f41b481b59a45aaad6426e719d49c29bf0c07/config.example.json#L4-L11)。作者列 35 個帳號；與現有 enabled X 名單逐字比對，8 個重疊、27 個待評估。精確字串比對不識別改名、大小寫差異或同公司多帳號，因此不直接將27個全部啟用。

| 組別 | 配置提案 | 來源 |
|---|---|---|
| 模型與工具官方 | 第一批已核准名單見下表 | 上游 labs 清單與下表身分來源 |
| 資訊整理者 | AK／TeksEdge 已核准；Rowan 保留待核 | 上游 bloggers 清單與下表身分來源 |
| 影像／影片 | 保留候選；使用者想加重視覺題目時啟用 | 上游 labs 清單 |
| Cursor | 先查是否與現有 Cursor 重複或改名 | 上游 cursor_ai／現有 Cursor |

## 已核准的來源

`twitter_radar` 與現役 enabled X 帳號不分大小寫去重。配置由 29 增到 43 個帳號（29 + 14），既有來源設定維持原樣。

| 帳號 | 用途 | 身分來源 |
|---|---|---|
| OpenAI | 模型與產品公告 | [OpenAI 官網](https://openai.com/)頁尾 |
| OpenAIDevs | API 與開發工具 | [開發者官網](https://developers.openai.com/)頁尾 |
| GoogleDeepMind | 模型與研究 | [DeepMind 官網](https://deepmind.google/)頁尾 |
| GoogleAIStudio | API 與工具入口 | [DeepMind 官號點名](https://x.com/GoogleDeepMind/status/2110039277442682916) |
| Alibaba_Qwen | Qwen 模型 | [Qwen GitHub 組織](https://api.github.com/orgs/QwenLM) twitter_username |
| Kimi_Moonshot | Kimi 模型 | [Moonshot 官網](https://www.moonshot.ai/)社交連結 |
| MiniMax_AI | MiniMax 模型與工具 | [MiniMax 官網](https://www.minimax.io/)與[GitHub](https://api.github.com/orgs/MiniMax-AI) |
| Zai_org | GLM 模型與工具 | [Z.ai GitHub 組織](https://api.github.com/orgs/zai-org) twitter_username |
| SpaceXSI | 原 xAI 品牌消息 | [x.ai 官網](https://x.ai/)頁尾目前指向 SpaceXSI |
| AIatMeta | Meta AI 模型與研究 | [Meta AI 官網](https://ai.meta.com/)頁尾 |
| MistralAI | Mistral 模型與工具 | [Mistral 官網](https://mistral.ai/)社交連結 |
| huggingface | 開源模型與平台 | [Hugging Face GitHub 組織](https://api.github.com/orgs/huggingface)與[官網](https://huggingface.co/) |
| _akhaliq | 論文與模型線索 | [AK 個人 HF 頁](https://huggingface.co/akhaliq)連到 X；個人帳號 |
| TeksEdge | 本地模型與硬體線索 | [個人 GitHub](https://api.github.com/users/TeksEdge) twitter_username；個人帳號 |

原候選 `MiniMax__AI` 改用 `MiniMax_AI`，`xai` 改用官網目前指向的 `SpaceXSI`。身分核對不保證訊息品質；部份帳號只確認到較舊貼文，首輪抓取會逐帳號記錄空結果／解析缺口，不宣稱已確認最新活動。

`rowancheung` 保留待核、不啟用：[Rundown 官網](https://www.therundown.ai/)本輪只連品牌 `therundownai`，尚未建立到個人 handle 的身分鏈。這不是判定其帳號是假或品質差。

視覺類候選，不啟用：Kling_ai、runwayml、pika_labs、LumaLabsAI、Hailuo_AI、higgsfield_ai、ViduAI_official、Alibaba_Wan、dreamina_ai、midjourney、krea_ai。

待驗身分：cursor_ai。是否仍為有效帳號、是否與現有 Cursor 重複，尚未查證；不得按新帳號計入啟用配置。其他未啟用候選仍需確認身分，不把作者推薦當來源品質。

## 抓取頻率與費用

這輪不增加排程。現有 shared collector 每日一次；兩套分析可多次讀池，不會因此重抓。若需要比每日更早的感知，另由使用者批准縮短 X 收集間隔；不能把分析頻率等同抓取頻率。

公開 actor metadata 回件：[ScrapeBadger API](https://api.apify.com/v2/acts/scrape.badger~twitter-tweets-scraper)，2026-10-14 查詢，最新 startedAt=2026-08-21 的 PAY_PER_EVENT 配置列 FREE 的 tweet event 為 US$0.00015／筆。新增 14 帳號、每帳號最多 10 筆、每日一次、30 日的事件費：14 × 10 × 1 × 30 × 0.00015 = **估計 US$0.63／30 日**，不是完整帳單上限。

[官方價格表](https://apify.com/scrape.badger/twitter-tweets-scraper/pricing)標示 Platform costs Included，同 Actor README 又提啟動費與空結果計費；兩者差異尚未用實際帳單釐清。現役程式每 handle 各一發，帶 `maxTotalChargeUsd=0.01`（`src/social_info/fetchers/twitter.py`）。按正常每日一次，14 × 30 × 0.01 = **US$4.20／30 日的名義 Actor 費限制**；程式沒有月累計硬閘，重跑、重試與平台費也不能用這個算式保證封頂。

使用者核准只用既有額度。relay 本輪唯讀 `/status` 顯示另一個已配置帳號餘額 372 美分（US$3.72），`/healthz` 回 `ok=true`；這是當時觀察值，不保證整月足夠。額度不足時呈現來源缺口，不自動充值或升級。增加頻率尚未批准。

## 額外服務

| 服務 | 本輪方案 | 來源 |
|---|---|---|
| X 抓取 | 共用現有 Apify；不加 AIsa X 後端 | 現役 twitter.py 抓取路徑 |
| 名稱判斷 | 本機上游規則先產候選；分析品質待自然觀察 | 上游 terms.py 純函式 |
| AIsa 名稱模型 | 選配、不啟用；若規則噪音影響選題再評估 | 上游 judge.py |
| AIsa SEO／Trends | 非本輪選題必要，不啟用 | 上游 usecases.py／review.py |
| GoAnyAPI | 非本輪必要，不啟用 | 上游 README 選配資料源 |
| 既有模型 | daily topic 照原分析路徑，不新增模型服務 | 現役 daily-topic workflow |

新增帳號使用現役 Apify 額度，不新增訂閱；daily topic 照原模型路徑。更大的資料輸入是否增加模型 token／耗時尚未量測，先以實際觀察判斷，不預設每次都分析全部池資料。

## 免費原工具的共用接線

使用者後續確認直接做：擴大資料池、備好原工具所需資料與歷史，並讓正式 digest 呈現新角度。原工具免費模組固定在 [456aeee817ba6a755e3db90bc298aead051945fb](https://github.com/zhuyansen/new-term-radar/tree/456aeee817ba6a755e3db90bc298aead051945fb)，不執行帶付費預設值的整套 `radar daily`。

| 原工具功能 | 現有覆蓋與本輪接法 | digest 可取用的內容 | 來源 |
|---|---|---|---|
| X 名稱差分 | 共用池已有正文，供原工具分析；不另抓 X | 多帳號提到的新名稱及貼文證據 | [sources.py](https://github.com/zhuyansen/new-term-radar/blob/456aeee817ba6a755e3db90bc298aead051945fb/radar/sources.py)、[現有配置](/sources.yml) |
| HF 新進榜 | 現有抓取按 likes7d；另抓 trendingScore，保留 models／Spaces 基準 | 新進榜模型與 demo、名稱副本整理；首次只報基準 | [sources.py](https://github.com/zhuyansen/new-term-radar/blob/456aeee817ba6a755e3db90bc298aead051945fb/radar/sources.py)、[現有抓取](/src/social_info/fetchers/huggingface.py) |
| GitHub 成長 | 保留 Trending／Trendshift／既有搜尋；新增近期建立的搜尋角度 | 建立時間、平均星數成長或相對前次增量的研究線索 | [breakout.py](https://github.com/zhuyansen/new-term-radar/blob/456aeee817ba6a755e3db90bc298aead051945fb/radar/breakout.py) |
| 目錄新收錄 | 新增 Glama、LobeHub Skills、OpenTools 的 sitemap 比較 | 新收錄工具／MCP／skill 頁面，不冒稱新發布或已驗工具品質 | [sitemaps.py](https://github.com/zhuyansen/new-term-radar/blob/456aeee817ba6a755e3db90bc298aead051945fb/radar/sitemaps.py)、[本輪配置](/new-term-radar.json) |
| Reddit 求助／評論痛點 | 原工具走付費服務，本輪不啟用 | 尚無這類新增內容 | [asks.py](https://github.com/zhuyansen/new-term-radar/blob/456aeee817ba6a755e3db90bc298aead051945fb/radar/asks.py)、[pains.py](https://github.com/zhuyansen/new-term-radar/blob/456aeee817ba6a755e3db90bc298aead051945fb/radar/pains.py) |
| 新網站／新賣家／收入 | 需要付費資料服務或 TrustMRR key，本輪不啟用 | 尚無這類新增內容 | [上游說明](https://github.com/zhuyansen/new-term-radar/blob/456aeee817ba6a755e3db90bc298aead051945fb/README.zh-CN.md) |
| SEO／Trends 回顧 | 需要付費服務，本輪不啟用 | 不提供搜尋量或市場熱度結論 | [usecases.py](https://github.com/zhuyansen/new-term-radar/blob/456aeee817ba6a755e3db90bc298aead051945fb/radar/usecases.py)、[review.py](https://github.com/zhuyansen/new-term-radar/blob/456aeee817ba6a755e3db90bc298aead051945fb/radar/review.py) |
| Search Console | 需要本人網站授權，本輪不啟用 | 不提供本人網站曝光增量 | [gsc.py](https://github.com/zhuyansen/new-term-radar/blob/456aeee817ba6a755e3db90bc298aead051945fb/radar/gsc.py) |

獨立收集入口由現有每日 pipeline 在一般来源存池後呼叫，也可單獨執行；不新增排程。新資料和來源健康進共用池，原工具另保存 HF／GitHub／sitemap 快照與觀察報告。digest 讀 raw 中的「新來源觀察」及原工具報告，按候選原文指出值得研究的原因與增量，與已有主題重疊時只交叉引用。

初次 HF 與 sitemap 抓取只建立比較基準，不能驗證「新進榜／新收錄」。同 URL 的既有來源與新工具不是兩份獨立證據。公開頁面抓取不使用付費 API，但仍受端點流量限制與站點規則；本輪選中的 robots 與 sitemap 在 2026-10-14 實測 HTTP 200，未據此保證之後每日成功。

## 啟用與觀察

14 個帳號每日一次的配置已獲使用者核准；首輪實抓只跑新增 `twitter_radar`，不重抓原來源。2026-10-14 首輪14次請求完成，回件中25則可解析貼文來自7個帳號，另7個帳號回`empty/no_results`；TeksEdge達10筆上限，不追加抓取。25則已存共用池與當日raw；6則較完整的`full_text`以已保存回件離線補存，不重抓。回件、池與raw對照是本輪驗證，不保證下一輪每帳號都有消息。

自然使用時追蹤每帳號回件、是否碰上限、以及使用者有沒有想研究的新題目。更早找到可寫題目的收益留待觀察；額外訂閱、充值、其他候選與增加頻率均未啟用。
