# deal-watch — 限時資訊盯梢地圖

## Destination

「限時資訊盯梢」產線的完整 spec：A 類主動盯梢（資料源、輪詢頻率、判定邏輯、Discord 推送、清單生命週期）全部決策拍板完畢，B 類 digest 掃描面補強形狀確定，可直接進 to-spec → to-tickets → implement。

## Notes

- 本 effort 屬 matt 群：全程不 invoke superpowers:* skill。
- 血緣：源自 2026-08-17 brainstorm（殘渣見 [../brainstorm-2026-08-17.md](../brainstorm-2026-08-17.md)）。框架已定：A 類（已知優惠盯門路）＝小時級＋推送、B 類（新優惠發現）＝日級 digest 補強即可。
- 既有資產：repo 根的 `WATCH.md` 是被動日級盯梢清單（entry 範本含 Why I care / Match keywords / Action on hit），schema 沿用優先於另起爐灶。
- Discord 推送是 memory `feedback_interface_is_claude_code`（介面就是 CC、不上外部通道）的例外條款啟用——使用者 2026-08-17 明確要求，僅限本產線、不外溢成通例。
- 通知設計必配三件套（沉底可見／計數 escalation／拍板回寫），見 memory `user_digest_consumption_top2_only`。
- runtime 選型注意：macOS TCC 擋 `~/Desktop` 下的 LaunchAgent（memory `reference_macos_tcc_desktop_launchagent`），排程落點要避開。
- 誤報噪音是本系統的存亡參數：Discord 通知被靜音＝整套死。

## Decisions so far

- [資料源可行性](tickets/001-data-source-feasibility.md) — Cursor forum + Reddit（皆 $0）+ X Apify（~$3/月）三源小時級可行；Threads 小時級出局、降觸發式補查
- [誤報預算與推送門檻](tickets/004-notification-budget-threshold.md) — 「具體可行動內容」才推；每輪 ≤3 則＋more；沉底一律存 SQLite（count≥3 升級）；供給鏈全滅一輪即告警
- [Runtime 與輪詢節奏](tickets/006-runtime-and-cadence.md) — 實作歸 `~/Desktop/projects/watchdogs` 第 2 隻 watcher（首隻 T2＝帶 LLM 判讀）、沿用其 launchd／Keychain／notify 慣例；24 小時每小時一輪
- [B 類 digest 補強形狀](tickets/007-btype-digest-scan-expansion.md) — 只加 WATCH.md Watched Topics 一條 entry、零改碼；兩產線獨立跑一個月再議
- [盯梢清單 schema 與生命週期](tickets/003-watchlist-schema-lifecycle.md) — WATCH.md 新 section＋CC 一句話入口；7 天到期＋Discord 續盯詢問；互動升級雙向（bot 收指令：續／停／暫停，最慢 1 小時生效）；種子僅 grok heavy 一條
- [Token 與搜尋預算](tickets/002-token-and-search-budget.md) — 免費池崩（relay 舊案作廢）；Haiku 判讀 ≈$11/月；搜尋引擎比推理貴 2–5 倍（Brave 最便宜 $24/月）→ 成本結構偏向「來源 API 直抓＋LLM 判讀」、搜尋引擎僅低頻補充；增補 002b＋使用者拍板：硬約束「只用訂閱或免費、不用 API credit」；判讀＝relay 訂閱腿 `gemini-3.7-flash-high`（ToS 風險使用者知情自擔）、備援 Zen 免費兩顆；spec 必帶「prompt 注入當前日期」P0；antigravity-credits 已關

## Not yet specified

（2026-08-17 霧區清空：判定層形狀由票 002/004/008 的決策覆蓋、剩 prompt 細節屬 spec 層；fetcher 共用問題由票 006「watchdogs 自洽、與 social-info 產線獨立」解掉。）

## Out of scope

- OpenClaw 復活（2026-07-05 收案、07-27 已清除；本 effort 燃料是排程不是對話，不相干）。
- 工作 Slack 通道。
- B 類補強以外的 digest 改版。
