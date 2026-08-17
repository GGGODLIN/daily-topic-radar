# 002 — 小時級盯梢產線的推理與搜尋供給盤點

- 票：wayfinder deal-watch #002
- 日期：2026-08-17
- 基準工作負載（本檔所有試算共用）：**每小時一輪 × 8 條盯梢主題**
  - 輪數 R = 24 × 30 = **720 輪/月**
  - 判讀次數 = 搜尋次數 = 720 × 8 = **5,760 次/月**

## 證據等級標示

| 標記 | 意思 |
|---|---|
| 【實測】 | 本次在這台機器上真的發出請求、貼了回應 |
| 【文件】 | 官方頁面 2026-08-17 抓下來的原文 |
| 【估算】 | 由【實測】token 量 × 【文件】單價推導，計算式在同段 |
| ⚠️【未驗證】 | 查不到或沒實測，不得當前提用 |

---

## §1 CLIProxyAPI relay 免費池現況（90 天期滿重驗）

### 1.1 重驗結論

**免費池已實質崩塌，不能當小時級產線的推理供給。** 舊案（memory `project_cliproxyapi_relay`，2026-07-26）寫的「OpenCode Zen 免費 5 顆 + NVIDIA NIM 免費 2 顆、`ds-flash` alias = Zen 優先 + NVIDIA 墊背跨家 fallback」— 這套 fallback 鏈**兩端都斷了**。

### 1.2 服務本身活著【實測】

```
$ ps aux | grep cli-proxy-api
linhancheng 10661 ... /Users/linhancheng/.cli-proxy-api/bin/cli-proxy-api --config .../config.yaml
$ ~/.cli-proxy-api/bin/cli-proxy-api --version
CLIProxyAPI Version: 7.2.75, Commit: e5741673, BuiltAt: 2026-07-14T15:37:22Z
$ curl -s -o /dev/null -w "%{http_code} %{time_total}" .../v1/models   →   200  0.0016s
```

launchd 常駐正常、config 今天 12:08 還被改過（`config.yaml.bak-pre-effort-probe-20260817`）。**壞的是上游供給，不是 relay。**

### 1.3 `ds-flash` 已死 — NVIDIA 腿 EOL【實測】

```
$ curl .../v1/chat/completions -d '{"model":"ds-flash",...}'
HTTP=410
{"type":"about:blank","title":"Gone","status":410,
 "detail":"The model 'deepseek-ai/deepseek-v4-flash' has reached its end of life
           on 2026-08-07T09:00:00Z and is no longer available."}
```

`deepseek-v4-flash`（NVIDIA NIM alias）同樣 410。**EOL 日期 2026-08-07，距今 10 天。** 這正是 90 天重驗規則要抓的東西 — 若直接引舊案，會把一條 8/7 就死掉的模型寫進 spec。

`nemotron-super-49b`（NVIDIA 另一顆）回 200，但 53.8 秒且 content 空。NVIDIA 這條腿實質不可用。

### 1.4 OpenCode Zen 免費顆：三顆熱門的全數 429【實測】

直接打 `https://opencode.ai/zen/v1`（繞過 relay，排除 relay 自身問題），同一個真實判讀 prompt 各跑 6 次、間隔 4 秒：

| 模型 | 可用率 | 延遲（實測範圍） | 備註 |
|---|---|---|---|
| `hy3-free` | **6/6** | 9.2–12.6s | JSON 正確、價格抽取正確 |
| `nemotron-3.5-lightning-free` | **6/6** | 8.0–28.5s | 正確，但尾延遲差 3.5 倍 |
| `nemotron-3-ultra-free` | **6/6** | 6.4–14.6s | 6 次裡 **1 次答 8390**（自行套用滿額折 300），其餘答 8690 |
| `laguna-s-2.1-free` | **1/6** | — | 5 次 `503 Upstream request failed: Endpoint is unavailable.` |
| `big-pickle` | **0/6** | — | 6 次 `429 FreeUsageLimitError: Rate limit exceeded` |
| `deepseek-v4-flash-free` | **0/6** | — | 同上 429 |
| `mimo-v2.5-free` | **0/6** | — | 同上 429 |

429 不是我打太快打出來的：relay 那一輪間隔 25 秒、直打 Zen 那輪間隔 4–6 秒，兩輪結果一致；且同一時刻其他免費顆是 200。錯誤訊息是 `Error from provider (Console)`，即 **Zen 上游的共享容量限流，不是本帳號的月額度**。

判讀品質本身沒問題（可用的三顆都抽對 8690），問題全在**供給的可得性**。

### 1.5 本機 config 已 stale【實測】

`~/.cli-proxy-api/config.yaml` 的 `opencode-zen` 段登記 5 顆，跟 Zen 現況對不上：

| config 有 | 現況 |
|---|---|
| `deepseek-v4-flash-free` | 429 |
| `mimo-v2.5-free` | 429 |
| `big-pickle` | 429 |
| `nemotron-3-ultra-free` | ✅ 可用 |
| `north-mini-code-free` | **已下架** — relay 回 `401 Model north-mini-code-free is not supported`，Zen `/v1/models` 也沒有 |

Zen 現在提供 7 顆免費（`GET https://opencode.ai/zen/v1/models`【實測】）：`big-pickle`、`deepseek-v4-flash-free`、`mimo-v2.5-free`、`hy3-free`、`nemotron-3-ultra-free`、`nemotron-3.5-lightning-free`、`laguna-s-2.1-free`。**config 少登記了現在唯二穩定的 `hy3-free` 跟 `nemotron-3.5-lightning-free`。**

Zen 官方對免費顆的定性【文件，https://opencode.ai/docs/zen/】：

> DeepSeek V4 Flash Free is available on OpenCode **for a limited time**. The team is using this time to collect feedback and improve the model.

7 顆免費模型定價表全欄位皆為 `Free`【文件】— 沒有月額度數字、也沒有 SLA。**「限時」是官方自己的用詞**，任何依賴它的排程都建立在隨時可撤的地基上。

### 1.6 被忽略的第三條腿：relay 的訂閱制模型【實測】

relay 現在服務 33 個 model id，其中 Codex OAuth 與 Antigravity OAuth 背後是**使用者已付費的訂閱**，不是免費池：

```
$ curl .../v1/chat/completions -d '{"model":"gpt-5.6-luna", ...判讀 prompt...}'
HTTP=200  T=2.54s
{"hit":true,"price":8690,"reason":"限時價低於9000元"}
usage: {"prompt_tokens":410,"completion_tokens":57}
```

`gpt-5.6-terra` 亦 200 / 2.4s。`gemini-3-flash`（Antigravity 線）200 / 9.0s 但本次回空 content。

`gpt-5.6-luna` 是目前 relay 上**又快又對又零邊際成本**的判讀腿（2.5s，vs 免費池最好的 hy3-free 9–13s）。有兩個 Codex 帳號：`codex-qwe70301@gmail.com-pro.json`（個人 Pro）與 `codex-f2306d4e-philip@akohub.com-team.json`（公司 team）。

**兩個必須先拍板的風險，不是我能決定的：**
1. 拿互動式訂閱（Codex Pro）跑 24×30 = 720 輪無人值守排程，是否踩 ToS。⚠️【未驗證】我沒查 OpenAI 現行條款。
2. 若誤用到 `philip@akohub.com` team 帳號，等於拿公司額度跑個人盯梢。relay `routing.strategy: fill-first` + 帳號 priority 決定實際落哪顆，**需要顯式綁個人帳號**。

### 1.7 §1 判準：什麼會推翻這個結論

- 若 Zen 的 429 是「今天剛好塞車」而非常態：**連跑 7 天、每天同時段採樣，可用率若回到 >95% 就推翻**。本次只有單日三輪（14:30–14:50）採樣，這是本節最大的證據弱點。
- 若 Zen 補上新的免費顆或提高容量，`hy3-free` 之外多兩顆穩定的，「免費池不可用」就要改寫成「免費池需要 N 顆輪替」。

---

## §2 走 Anthropic Haiku 的月成本量級

### 2.1 官方單價【文件】

來源：https://docs.claude.com/en/docs/about-claude/pricing 與 https://www.anthropic.com/pricing（兩頁 2026-08-17 抓取，數字一致）

| 項目 | Claude Haiku 4.5 |
|---|---|
| Base input | **$1 / MTok** |
| Output | **$5 / MTok** |
| 5m cache write | $1.25 / MTok |
| 1h cache write | $2 / MTok |
| Cache hit / refresh | $0.10 / MTok |
| Batch input / output | $0.50 / $2.50 per MTok |

Model id【文件，https://docs.claude.com/en/docs/about-claude/models/overview】：`claude-haiku-4-5-20251001`，alias `claude-haiku-4-5`。目前的 latest 陣容是 Fable 5 / Opus 5 / Sonnet 5 / **Haiku 4.5** — Haiku 4.5 仍是最新的 Haiku。

### 2.2 每次呼叫的 token 量（實測 anchor）

不用猜。同一個盯梢判讀 prompt 打 relay 的 `gpt-5.6-luna`【實測】：

```
prompt_tokens = 410      completion_tokens = 57
（內容 = 判讀指令 + 盯梢主題 + 1 條搜尋結果）
```

拆解與外推：

- 指令 + 主題描述 ≈ **250 tok**
- 每條搜尋結果（標題 + 摘要）≈ **160 tok**
- 每主題取 5 條結果 → input ≈ 250 + 5×160 = 1,050 tok
- **取 1,200 tok** 吸收 Claude tokenizer 對中文的差異（⚠️【未驗證】我沒有 Anthropic API key，無法用 `count_tokens` 拿 Claude 的真值；此處是 luna tokenizer 實測值 + 保守放大）
- output 實測 57 tok → **取 150 tok** 留 reason 欄位餘裕

### 2.3 四種算法的月成本【估算】

**A. 無 cache、每主題一次呼叫（基準）**

```
input  = 5,760 × 1,200 = 6,912,000 tok = 6.912 MTok × $1 = $6.91
output = 5,760 ×   150 =   864,000 tok = 0.864 MTok × $5 = $4.32
                                                    合計 = $11.23 / 月
```

**B. 加 5 分鐘 prompt cache（同一輪 8 次呼叫連發，指令段 250 tok 可快取）**

```
cache write = 720 × 250            = 0.180 MTok × $1.25 = $0.23
cache read  = 720 × 7 × 250        = 1.260 MTok × $0.10 = $0.13
fresh input = 5,760 × 950          = 5.472 MTok × $1.00 = $5.47
output                                                   = $4.32
                                                    合計 = $10.14 / 月
```

→ **cache 只省 $1.09（9.7%）。** 原因是可快取的固定前綴只佔 input 的 21%，搜尋結果每輪都不一樣。別為了 cache 增加實作複雜度。

**C. 一輪 8 主題打包成一次呼叫**

```
input  = 720 × (250 + 8×800) = 720 × 6,650 = 4.788 MTok × $1 = $4.79
output = 720 × (8 × 150)     = 720 × 1,200 = 0.864 MTok × $5 = $4.32
                                                        合計 = $9.11 / 月
```

**D. Batch API（單價砍半）**

A × 0.5 = **$5.62 / 月**。⚠️【未驗證】官方 pricing 頁我抓到的段落沒寫完成時間 SLA；非同步批次對「小時級盯梢」的新鮮度是否可接受，需另外確認。

**上限敏感度**（每主題 10 條結果、output 放到 300 tok）：

```
input  = 5,760 × 2,050 = 11.808 MTok × $1 = $11.81
output = 5,760 ×   300 =  1.728 MTok × $5 =  $8.64
                                     合計 = $20.45 / 月
```

### 2.4 §2 結論

**Haiku 4.5 跑這條產線的推理成本落在 $9–21 / 月，量級就是「每月十幾美金」。** 換算每輪約 **1.6 美分**（$11.23 ÷ 720）。

這個數字的真正意義在下一節：**它比搜尋便宜得多，推理不是成本瓶頸。**

---

## §3 搜尋額度盤點

### 3.1 免費額度與付費單價【文件，全部 2026-08-17 抓取】

| 來源 | 免費額度 | 要綁卡？ | 付費單價 | 出處 |
|---|---|---|---|---|
| **Anthropic 內建 WebSearch** | 無（pricing 頁未列免費額度） | — | **$10 / 1,000 次搜尋** + 搜尋結果照 input token 計費 | [docs pricing](https://docs.claude.com/en/docs/about-claude/pricing)、[web search tool](https://docs.claude.com/en/docs/agents-and-tools/tool-use/web-search-tool) |
| **Brave Search API** | **$5 credits/月 → 1,000 次** | **要**（官方 FAQ：free plan 的卡「only used to confirm your identity and will not be charged」） | **$5 / 1,000 requests**；容量 50 QPS | [brave.com/search/api](https://brave.com/search/api/) |
| **Exa `/search`** | **$10 credits/月 → ~1,428 次**；另有一次性 $20 註冊金（≈2,800 次） | **不用**（"No payment method required"） | **$7 / 1,000 requests**（含前 10 筆結果）；免費層 5 search QPS | [docs.exa.ai/reference/pricing](https://docs.exa.ai/reference/pricing)、[exa.ai/pricing](https://exa.ai/pricing) |
| **Tavily** | **1,000 credits/月** | **不用**（"No credit card required"） | PAYG **$0.008 / credit**；basic search = **1 credit**、advanced = **2 credits**。月費方案：Project 4,000/$30、Bootstrap 15,000/$100、Startup 38,000/$220、Growth 100,000/$500 | [docs.tavily.com/documentation/api-credits](https://docs.tavily.com/documentation/api-credits) |

### 3.2 對舊案的兩處推翻

memory `_index_search_api_landscape.md`（2026-06-21 deep research，207 agents / 11.4M tokens）的兩條核心數字**現在是錯的**：

| 舊案寫 | 現況【文件】 | 差距 |
|---|---|---|
| 「Exa **20K req/月**真 free」→ 據此把 Exa 排為 free-tier 約束下**首選** | Exa 免費層 = **$10 credits/月 ≈ 1,428 次**（官方原文：「New accounts get $20 in free credits (around 2,800 searches) and the Free Tier adds **$10 in credits every month**」） | **縮水 14 倍。** 「free-tier-only 選 Exa」這條建議的前提已消失 |
| Brave「$5/月 credits + 綁卡 + **超量自動扣**」（引 implicator.ai 2026-06-08） | Brave 官方 FAQ 現在明寫 free plan 的卡「will not be charged」 | ⚠️【未驗證】我沒去驗實際扣款行為，只能說**官方說法與舊案第三方轉述不一致**。要用 Brave 就自己去 dashboard 確認一次 |

Tavily 的 1,000 credits/月與 Brave 的 $5/1k 兩項，舊案與現況一致，維持有效。

### 3.3 小時級輪詢下的免費額度換算【估算】

需求 = **5,760 次搜尋/月**（8 主題 × 720 輪）。

| 情境 | 可用次數/月 | 換算成 8 主題的輪詢頻率 |
|---|---|---|
| 單一家免費（Brave 或 Tavily） | 1,000 | 每 **5.8 小時**一輪 |
| 單一家免費（Exa） | 1,428 | 每 **4.0 小時**一輪 |
| **三家全疊**（Brave+Exa+Tavily） | **3,428** | 每 **2.1 小時**一輪 |
| 小時級（目標） | **5,760** | — |

計算式：`可用次數 ÷ 8 主題 = 可跑輪數`；`720 輪 ÷ 可跑輪數 = 每輪間隔小時數`。例：3,428 ÷ 8 = 428.5 輪 → 720 ÷ 428.5 = 1.68 → 實務取整為每 2 小時（8×12×30 = 2,880 次，仍在 3,428 以內、留 16% 餘裕）。

**這是本檔最硬的約束：任何一家的免費額度，甚至三家全疊，都撐不起 8 主題 × 小時級。** 免費額度的天花板全落在 1,000–1,430 次/月這個帶，而小時級需要 5,760。

### 3.4 付費時的月成本【估算，5,760 次】

| 來源 | 計算式 | 月成本 |
|---|---|---|
| **Brave** | 5,760 ÷ 1,000 × $5 = $28.80，扣每月 $5 免費 credits | **$23.80** |
| **Exa** | 5,760 ÷ 1,000 × $7 = $40.32，扣每月 $10 免費 credits | **$30.32** |
| **Tavily（basic，1 credit/次）** | 5,760 credits − 1,000 免費 = 4,760 × $0.008 | **$38.08** |
| **Tavily（advanced，2 credits/次）** | 11,520 − 1,000 = 10,520 × $0.008 | **$84.16** |
| **Anthropic WebSearch** | 5,760 ÷ 1,000 × $10 | **$57.60**（不含結果回灌的 token） |

排序：**Brave < Exa < Tavily(basic) < Anthropic 內建 < Tavily(advanced)**。

### 3.5 §3 的關鍵不對稱

```
推理（Haiku 4.5）    ≈ $11 / 月
搜尋（最便宜的 Brave）≈ $24 / 月
搜尋（Anthropic 內建）≈ $58 / 月
```

**搜尋成本是推理的 2–5 倍。** 直覺會覺得「LLM 判讀很貴、搜尋很便宜」，實際反過來 — 省錢的旋鈕在搜尋源與輪詢頻率，不在換更小的模型。把模型從 Haiku 換成免費池，最多省 $11；把搜尋從 Anthropic 內建換成 Brave，省 $34。

### 3.6 一個耦合限制

Anthropic 內建 WebSearch 是 **server tool**，只能在 Messages API 裡搭 Claude 模型用。**選了非 Anthropic 的推理源，就用不到它** — 搜尋源與推理源在這一格不是自由組合。

⚠️【未驗證】Haiku 4.5 是否在 `web_search` server tool 的支援模型清單內：官方 web search tool 頁與 models overview 頁我都抓下來全文搜過，**兩頁都沒有列出支援模型清單，也沒提到 Haiku**。走組合 3 之前必須先實測一次。

---

## §4 可行的供給組合

### 組合 1 — 免費疊加 + 訂閱推理（$0 增量，但達不到小時級）

| 面 | 選型 | 依據 |
|---|---|---|
| 搜尋 | Brave 1,000 + Exa 1,428 + Tavily 1,000 = **3,428 次/月** | §3.1【文件】 |
| 推理 | relay `gpt-5.6-luna`（Codex OAuth 訂閱） | §1.6【實測】2.5s、判讀正確 |
| 頻率 | **每 2 小時一輪**（8 主題 × 12 輪/天 = 2,880 次/月） | §3.3 |

**月成本：$0 增量。**

代價（三項，都不小）：
1. **達不到票面要的小時級** — 降到 2 小時。
2. 三家 key 輪替 + 三套不同 response schema 的膠水程式碼，是這三個組合裡工程量最大的。
3. **Codex 訂閱跑無人值守排程的 ToS 灰區**（§1.6），且必須顯式綁 `qwe70301@gmail.com` 個人帳號、不能落到公司 team 帳號。

不要把 Zen 免費池當推理源：§1.4 實測 7 顆裡 3 顆全 429、1 顆 5/6 掛 503，只有 `hy3-free` 與 `nemotron-3.5-lightning-free` 穩，而官方自稱「limited time」。當 `gpt-5.6-luna` 的墊背可以，當主力不行。

### 組合 2 — Brave + Haiku 4.5（真小時級、可預測）★ 推薦

| 面 | 選型 | 月成本 |
|---|---|---|
| 搜尋 | Brave Search API，$5/1k，5,760 次 | **$23.80** |
| 推理 | Anthropic `claude-haiku-4-5`，估算 A | **$11.23** |
| | **合計** | **≈ $35 / 月**（敏感度區間 $33–45） |

為什麼推薦：
- 兩邊都是第一方 API、PAYG 無硬上限，額度不會在半夜靜默斷炊。
- 沒有 ToS 灰區、沒有「limited time」。
- Brave Search 50 QPS，需求是 8 次/小時 — 容量餘裕 4 個數量級。
- **每輪成本 = $35 ÷ 720 ≈ $0.049**。輪詢頻率是線性旋鈕：改 2 小時直接砍半到 $17.5。

已知代價：Brave 要綁信用卡（§3.2 有一條未解的扣款疑慮）。

### 組合 3 — Anthropic 一站式（最少膠水、最貴）

| 項 | 計算式 | 月成本 |
|---|---|---|
| 內建 WebSearch | 5,760 ÷ 1,000 × $10 | $57.60 |
| 搜尋結果回灌 input | 5,760 × 1,500 tok = 8.64 MTok × $1 | $8.64 |
| 判讀 output | 5,760 × 150 tok = 0.864 MTok × $5 | $4.32 |
| | **合計** | **≈ $70 / 月** |

一個 API、一組帳單、零整合程式碼 — 但**是組合 2 的 2 倍價**，而且卡在 §3.6 那個未驗證點（Haiku 4.5 是否支援 web_search server tool）。**除非先實測通過，否則不要把它寫進 spec。**

### 三者對照

| | 組合 1 | 組合 2 ★ | 組合 3 |
|---|---|---|---|
| 月成本 | $0 增量 | ≈ $35 | ≈ $70 |
| 能否小時級 | ❌ 只到 2 小時 | ✅ | ✅ |
| 整合工程量 | 高（3 家搜尋 + relay） | 中（1 家搜尋 + 1 API） | 低 |
| 供給穩定性 | 低（訂閱 ToS + 免費層 limited time） | 高 | 高 |
| 阻擋項 | Codex ToS 未驗、帳號綁定 | Brave 綁卡 | Haiku web_search 支援未驗 |

**建議路徑：組合 2 起步。** 把「輪詢頻率」當第一旋鈕而非「換便宜模型」— §3.5 已證明省錢空間在搜尋側。若之後想壓成本，先降頻（線性省），再考慮把 `gpt-5.6-luna` 換掉 Haiku（最多再省 $11）。

---

## §5 未驗證項與開放問題

必須在進 spec 前收掉的：

1. ⚠️ **Haiku 4.5 是否支援 `web_search` server tool** — 官方兩頁都沒列支援模型清單。擋住組合 3。
2. ⚠️ **Codex 訂閱跑無人值守排程的 ToS** — 沒查 OpenAI 條款。擋住組合 1。
3. ⚠️ **Anthropic Batch API 的完成時間 SLA** — 抓到的 pricing 段落沒寫。決定 $5.62/月 那條路能不能走。
4. ⚠️ **Brave free plan 是否真的不扣卡** — 官方 FAQ 說不扣，舊案引第三方說會扣，兩邊沒對上。
5. ⚠️ **Claude tokenizer 對中文的實際 token 量** — 本檔 input 用 gpt-5.6-luna 實測值放大到 1,200 tok；沒有 Anthropic key 跑 `count_tokens` 驗證。若中文實際多 30%，§2 的數字要 ×1.2 左右。
6. ⚠️ **Tavily 的 QPS / rate limit** — 只查到 credit 定價，沒查併發限制。
7. ⚠️ **Zen 429 是否為常態** — 本次只在 2026-08-17 14:30–14:50 採樣三輪。要下「免費池不可用」的定論，該跨天採樣。

順手可做的維護（不擋 spec）：
- `~/.cli-proxy-api/config.yaml` 的 `opencode-zen` 段：移除已下架的 `north-mini-code-free`、`ds-flash`/`deepseek-v4-flash`（NVIDIA EOL），補上現在唯二穩定的 `hy3-free` 與 `nemotron-3.5-lightning-free`。
- memory `_index_search_api_landscape.md` 需更正 Exa 免費額度（20K → ~1,428/月）；memory `project_cliproxyapi_relay` 需更正免費池狀態。

## 附：本檔用到的一手來源

| 來源 | URL |
|---|---|
| Anthropic 定價（docs） | https://docs.claude.com/en/docs/about-claude/pricing |
| Anthropic 定價（官網） | https://www.anthropic.com/pricing |
| Anthropic 模型總覽 | https://docs.claude.com/en/docs/about-claude/models/overview |
| Anthropic web search tool | https://docs.claude.com/en/docs/agents-and-tools/tool-use/web-search-tool |
| Brave Search API | https://brave.com/search/api/ |
| Exa 定價（docs） | https://docs.exa.ai/reference/pricing |
| Exa 定價（官網） | https://exa.ai/pricing |
| Tavily credits & pricing | https://docs.tavily.com/documentation/api-credits |
| OpenCode Zen docs | https://opencode.ai/docs/zen/ |
| OpenCode Zen models | https://opencode.ai/zen/v1/models |
| 本機 relay | `http://127.0.0.1:8317`、`~/.cli-proxy-api/config.yaml` |
