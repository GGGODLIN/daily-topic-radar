# 研究選題雷達的來源與服務配置方案

## 本輪已批准的範圍

使用現有已啟用來源，抓取一次，供共用資料池、daily topic 與選題雷達取用。保存抓取端已取得的完整正文；沒有全文的來源保留摘要與資料限制，不追加抓頁請求。新雷達只讀本機資料，不呼叫新付費 API。新增帳號與額外服務在本文件規劃，尚未啟用。

## 推薦帳號

來源是 [new-term-radar 固定版本的範例配置](https://github.com/zhuyansen/new-term-radar/blob/dd2f41b481b59a45aaad6426e719d49c29bf0c07/config.example.json#L4-L11)。作者列 35 個帳號；與現有 enabled X 名單逐字比對，8 個重疊、27 個待評估。精確字串比對不識別改名、大小寫差異或同公司多帳號，因此不直接將27個全部啟用。

| 組別 | 配置提案 | 來源 |
|---|---|---|
| 模型與工具官方 | 作為第一批候選，待帳號有效性確認 | 上游 labs 清單 |
| 資訊整理者 | 作為第一批候選，與官方交叉觀察 | 上游 bloggers 清單 |
| 影像／影片 | 保留候選；使用者想加重視覺題目時啟用 | 上游 labs 清單 |
| Cursor | 先查是否與現有 Cursor 重複或改名 | 上游 cursor_ai／現有 Cursor |

第一批候選，不啟用：

- 官方：OpenAI、OpenAIDevs、GoogleDeepMind、GoogleAIStudio、Alibaba_Qwen、Kimi_Moonshot、MiniMax__AI、Zai_org、xai、AIatMeta、MistralAI、huggingface。
- 整理者：_akhaliq、TeksEdge、rowancheung。

視覺類候選，不啟用：Kling_ai、runwayml、pika_labs、LumaLabsAI、Hailuo_AI、higgsfield_ai、ViduAI_official、Alibaba_Wan、dreamina_ai、midjourney、krea_ai。

待驗身分：cursor_ai。是否仍為有效帳號、是否與現有 Cursor 重複，尚未查證；不得按新帳號計入啟用配置。其他候選也只沿用作者提供的名字，啟用前需確認官方身分與近況，不把作者推薦當已核實的來源品質。

## 抓取頻率與費用

這輪不增加排程。現有 shared collector 每日一次；兩套分析可多次讀池，不會因此重抓。若需要比每日更早的感知，另由使用者批准縮短 X 收集間隔；不能把分析頻率等同抓取頻率。

公開 actor metadata 回件：[ScrapeBadger API](https://api.apify.com/v2/acts/scrape.badger~twitter-tweets-scraper)，2026-10-14 查詢，最新 startedAt=2026-08-21 的 PAY_PER_EVENT 配置列 FREE 的 tweet event 為 US$0.00015／筆，其他 tier 更低。isPPEPlatformUsagePaidByUser 在前一價格設定為true；本估算不涵蓋平台額外使用費，也未查本人帳號方案／剩餘額度或實際帳單。

第一批15帳號、每帳號最多10筆、每日一次、30日的 tweet event 算式：15 × 10 × 1 × 30 × 0.00015 = **估計 US$0.675／月**。這是全數每次都達筆數上限的事件費估算，不是完整帳單上限。

如果改成每日三次，沿用相同15帳號與10筆限制：15 × 10 × 3 × 30 × 0.00015 = **估計 US$2.025／月**，平台額外費未計。這個頻率未批准。實際足夠的每帳號筆數需觀察截斷，不能為維持低費用宣稱已覆蓋所有貼文。

## 額外服務

| 服務 | 本輪方案 | 來源 |
|---|---|---|
| X 抓取 | 共用現有 Apify；不加 AIsa X 後端 | 現役 twitter.py 抓取路徑 |
| 名稱判斷 | 本機上游規則先產候選；分析品質待自然觀察 | 上游 terms.py 純函式 |
| AIsa 名稱模型 | 選配、不啟用；若規則噪音影響選題再評估 | 上游 judge.py |
| AIsa SEO／Trends | 非本輪選題必要，不啟用 | 上游 usecases.py／review.py |
| GoAnyAPI | 非本輪必要，不啟用 | 上游 README 選配資料源 |
| 既有模型 | daily topic 照原分析路徑，不新增模型服務 | 現役 daily-topic workflow |

本輪額外服務費：沒有新增計費呼叫；這不代表既有 Apify 或 daily topic 免費。更大的資料輸入是否增加模型 token／耗時尚未量測，先以實際觀察判斷，不預設每次都分析全部池資料。

## 待使用者核准的啟用方案

接線與本機驗證後，再決定是否：啟用第一批15帳號、維持每日一次或增加頻率、訂出完整帳單的月增額上限。推薦先維持現有頻率，以第一批候選的出題增量與資料截斷率判斷，不一次啟用全部名單。
