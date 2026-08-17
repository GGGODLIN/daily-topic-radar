---
status: closed
type: grilling
blocked-by: []
---

## Question

盯梢清單的 schema 與生命週期：沿用 `WATCH.md` entry 範本還是擴充（主動盯梢需要的欄位：查詢語句、到期日、命中判準）？到期機制怎麼設（預設 14 天自動退場？退場前的續盯拍板迴路怎麼接）？順帶收第一批種子：除了「grok heavy 優惠重現門路」，使用者現在還想盯什麼？

## Resolution

2026-08-17 grilling 定案：
- **落點**：既有 `WATCH.md` 加新 section（如 `## Active Deal Watch`），與被動 Watched Topics 分開；欄位自既有範本擴充（查詢語句、到期日、命中判準、通知歷史），細節 spec 階段定。
- **入口**：跟 CC 說一句「幫我盯 X」、CC 代寫 entry；不做獨立 CLI。
- **生命週期**：每條預設 **7 天**到期；到期發 Discord 詢問「續盯？」，回覆即延 7 天、無回應自動退場並留一行退場紀錄。
- **互動升級（使用者主動提出）**：Discord 通道要**雙向**——回覆可執行續盯／暫停／停止。架構含意：webhook 不能收訊，改用 bot token、每小時輪詢時順便讀 channel 撿指令；指令生效最慢一輪（1 小時），即時操作 fallback＝直接跟 CC 說。⚠️ 「1 小時延遲可接受」是假設、使用者未明確反駁，spec 前再對一次。影響票 005（webhook → bot）。
- **第一批種子**：僅「grok heavy 優惠重現門路」一條。