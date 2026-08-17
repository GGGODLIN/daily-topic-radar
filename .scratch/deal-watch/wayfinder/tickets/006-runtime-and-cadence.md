---
status: closed
type: grilling
blocked-by: [001, 002]
---

## Question

執行 runtime 與輪詢節奏：跑在哪（launchd？注意 TCC 擋 ~/Desktop；cron？CC headless／Workflow？）、多密（每小時？醒著的時段密、夜間疏？）、失敗重試與斷網韌性要做到什麼程度（參照 memory `reference_launchd_wakeup_dns_race` 的教訓）。依 001 的資料源路線與 002 的預算結論拍板。

## Resolution

2026-08-17 grilling 定案（大半由慣例對齊解掉——考古發現 `~/Desktop/projects/watchdogs` 是現成的「高頻 watch＋主動 push」類別框架，現任 apple-refurb-tw 上線運行中）：
- **落點＝watchdogs 第 2 隻 watcher**（使用者拍板）：沿用 per-watcher 目錄自洽（fetch/diff/notify/commit）、per-watcher launchd plist、Discord secret 走 macOS Keychain（`watchdogs/<name>` service）慣例；README「等第 2 隻再抽 lib」時機成立。
- **打破「T1 零 LLM」原則**：deal-watch 是首隻 T2（帶 LLM 判讀），README 分類表要補 tier 定義。
- **節奏＝24 小時每小時一輪**（使用者拍板）；到期「續盯？」詢問掛 09:00 那輪。
- 失敗韌性細節（launchd 喚醒 DNS race 教訓、retry 策略）spec 階段定，參照 memory `reference_launchd_wakeup_dns_race`。
- wayfinder 板子留在 social-info `.scratch`（歷史脈絡），實作 repo 歸 watchdogs。