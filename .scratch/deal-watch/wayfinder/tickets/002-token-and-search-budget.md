---
status: closed
type: research
blocked-by: []
---

## Question

小時級盯梢的推理與搜尋預算怎麼供給？三個子問：(1) CLIProxyAPI relay 免費池現況——還活著嗎、可用模型、穩定度（memory 評估已超過 90 天，照時效規則必須重查）；(2) 走 Anthropic haiku 的話每月成本量級估算（附計算式）；(3) 搜尋額度才可能是真瓶頸——WebSearch／SERP／第三方 search API 在小時級輪詢下的 quota 與費用盤點（memory `_index_search_api_landscape`：Brave > Exa > Tavily）。產出：可行供給組合 2–3 案＋各自月成本，附引用。

## Resolution

詳見 [research/002-token-search-budget.md](../research/002-token-search-budget.md)（2026-08-17，含實測與 7 項未驗證擋門）。結論：
- **CLIProxyAPI 免費池崩了、舊案作廢**：NVIDIA 腿 410 EOL（2026-08-07）、Zen 熱門免費模型全 429／503，僅 2 顆穩定但 8–28 秒延遲且官方自稱 limited time。免費推理腿不可作為產線基礎。
- relay 訂閱腿（gpt-5.6-luna）實測快且準，但無人值守排程的 ToS 未查＋帳號綁定風險（不得落到公司帳號），列為未驗證選項。
- **Haiku 4.5 官方價 $1/$5 per MTok**：8 主題小時級 ≈ $11.23/月（區間 $9–21）；prompt cache 僅省 9.7% 不值得加複雜度。
- **搜尋比推理貴 2–5 倍**：Brave $23.8 < Exa $30.3 < Tavily $38.1 < Anthropic 內建 $57.6／月；免費額度四家都只撐每 2 小時一輪。
- 推薦組合：Brave + Haiku ≈ $35/月；最擋路未驗證項＝Haiku 是否支援 web_search server tool（未實測前一站式組合不得進 spec）。
- 順帶更正兩處舊 memory：Exa free tier 已縮 14 倍（「free-tier 首選 Exa」前提消失）；`~/.cli-proxy-api/config.yaml` 登記的免費模型已過期。

### 增補（2026-08-17，使用者提出）：Gemini Flash via 訂閱

使用者方向：推理判讀改走自己的 Gemini Flash（吃 Google AI Pro 訂閱額度，qwe70301 個人帳），條件＝要能觀察消耗量＋設計備援。研究補件 [research/002b-gemini-flash.md](../research/002b-gemini-flash.md)（2026-08-17）結論：
- **訂閱腿（relay Antigravity OAuth）技術全通但明文違約**：Antigravity ToS §6 禁第三方工具，2026-02～05 有真實執法（403 TOS_VIOLATION、二犯永久封禁整包 AI Pro 服務）→ 出局。
- **改走付費 Gemini API key**：`gemini-3.1-flash-lite`（唯一 reasoning=0 的 flash）實測 p50 2.9s，以實測 token 量估 **$0.16–0.79/月**（Batch 再砍半），比 Haiku $1.82 便宜 → 判讀供給主案。免費 key 出局（250 RPD 不夠＋資料進訓練）。
- 備援鏈：Gemini flash-lite（付費 key）→ Haiku 4.5。
- 消耗觀測：keeper SQLite 唯讀直查（`/opt/homebrew/var/cpa-usage-keeper/app.db?mode=ro`、含 reasoning_tokens 與 per-key 歸帳）；relay usage-queue 已被 keeper 抽乾、keeper HTTP API 有 CSRF 擋 curl。
- **Spec 必帶約束（P0）**：prompt 必注入當前日期（否則 flash-lite 把 deadline 編成 2024，3/3 復現、靜默錯）。
- 舊數字勘誤：「AI Pro = 1,500 req/day with Gemini CLI」已過期（Gemini CLI 2026-06-18 停服 AI Pro）。
- 遺留問號（建議不追）：官方 Antigravity CLI 是否支援 headless——付費 key 月費 <$1，追不划算。
- 界外發現（另案、非本 effort）：relay `antigravity-credits: true` 會在額度用盡時無通知自動扣購買的 AI credits，關不關待使用者拍板。

### 使用者拍板（2026-08-17，override 研究建議）

- **硬約束：本產線不用任何 pay-per-use LLM API credit**——「要嘛用訂閱、要嘛用免費 token」。付費 Gemini API key 主案與 Haiku 備援**皆出局**。
- **判讀供給＝relay 訂閱腿（Antigravity OAuth）**：ToS §6 違約風險與封禁執法紀錄已完整告知（見上），使用者知情選擇、風險自擔。
- **模型＝`gemini-3.7-flash-high`**（使用者選，理由：比 flash-lite 強、留一點 agentic 餘裕）：實測 5.54s、每發隱藏 reasoning ≈283 token（訂閱腿下是額度消耗非金錢）；⚠️ 實作時注意 relay 模型 ID 與實際檔位有系統性錯位，先以 displayName 驗明正身；「prompt 注入當前日期」P0 約束不因換模型而免除、實作時對 3.7 重測日期 bug。
- **備援鏈（合乎硬約束的重設）**：3.7-flash-high（訂閱）→ Zen 免費穩定兩顆 `hy3-free`／`nemotron-3.5-lightning-free`（8–28s，小時級批次可接受）→ 連續失敗跳輪＋記入 log、連續 N 輪升級告警。relay Codex 腿（gpt-5.6）**不入鏈**：掛公司帳號、2026-08-18 離職後失效。
- **`antigravity-credits` 已關**（2026-08-17 實測：config 改 false → launchctl kickstart 重啟 → management API 回讀 `"antigravity-credits": false`）——額度用盡即停、不再自動扣購買的 credits。