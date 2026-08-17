# 002b — 判讀推理改走自己的 Gemini Flash（可行性 / 額度 / 觀測 / 備援）

**探測日期**：2026-08-17（台北時間 15:10–15:35）
**探測者**：Claude Opus 5，read-only（未動 relay config、未重啟服務）
**證據分級**：`【實測】`= 本機真跑過並附輸出｜`【文件】`= 官方 / repo 文件宣稱｜`【推測】`= 未驗證的推論

> ⚠️ 本檔含機器特定路徑，屬個人 memory 性質、不進 commit doc。所有金鑰只寫變數名與檔案位置，不寫值。

---

## 一句話結論

**技術面全部驗證通過（延遲、額度、品質都夠），但這條腿被 Google 逐字點名禁止、而且真的執法過。建議改走付費 Gemini API key——每月約 $0.5，比你冒的風險便宜太多。**

### 為什麼建議改路（不是模糊的「有風險」，是三件具體的事）

1. **Antigravity 條款逐字點名這個模式。**【文件】<https://antigravity.google/terms> 第 6 條：
   > "Using **third party software, tools, or services to access the Service** (e.g. using OpenClaw with Antigravity OAuth) **is a breach of this Agreement.** Such actions **may be grounds for suspension or termination of your account.**"

   括號裡的例子（第三方工具 + Antigravity OAuth）與 CLIProxyAPI 是同一類。這不是灰色地帶。
2. **真的執法過，而且封鎖點正是這條腿打的那個 host。** Google DevRel 官方公告（gemini-cli discussion #20632, 2026-02-27）承認 2026-02～05 有一波 Antigravity 封號，起因逐字是 "**the use of 3rd party tools or proxies to access Antigravity resources and quotas**"；實際回應是 `403 PERMISSION_DENIED / reason: TOS_VIOLATION / domain: cloudcode-pa.googleapis.com`。**官方政策：第二次違規永久封禁。**
3. **你什麼都沒換到。** 你的量級是**每月 $0.16–0.79**（付費 `gemini-2.5-flash-lite` / `3.5-flash-lite`，用實測 token 量算）。用「AI Pro 額度可能被停 + Antigravity/Gemini CLI/Code Assist 一起失效 + 二犯永久」去換不到一美元，任何風險偏好下都不划算。

### 好消息：技術面的結論全部可以搬過去

- **延遲**：p50 2.9s、p95 3.4s、一輪 10 條並發 2.9 秒跑完（付費 API key 走 `generativelanguage.googleapis.com`，延遲數字需重測，但同級模型不會差一個數量級）
- **模型選擇**：`gemini-3.1-flash-lite` 是唯一 **reasoning token = 0** 的一顆，判讀 10/10 正確、比 thinking flash 省 6–24 倍 token。這個選型結論直接適用付費路。
- **成本**：$0.16–0.79/月（Batch API 再砍半）。比 Haiku 4.5 的 $1.82 更便宜。

### 兩個不管走哪條路都要修的 bug

1. ⚠️ **不給當前日期，flash-lite 會把 deadline 編成 2024 年**（3/3 復現、temperature=0 確定性）。對「限時優惠盯梢」是致命且靜默的錯誤。**判讀 prompt 必須注入今天日期**（見 1.5）。
2. ⚠️ **免費 API key 不可行**：官方 CLI 額度文件列免費層 **250 requests/day**，你要 240 → **96% 額度**，一次 retry 就爆；而且免費層的 prompt 與 output **會進 Google 訓練流程且有人工審閱**。**要走 API key 就開 billing**（見 5.3）。

> 📌 **票 002 要改的引用**：若引用了「AI Pro = 1,500 req/day with Gemini CLI」——**Gemini CLI 已於 2026-06-18 停止服務 AI Pro**，那個數字已過期（見 2.2）。

---

## 子問 1｜本機 relay 現成路線實測

### 1.1 服務現況

| 項目 | 值 | 來源 |
|---|---|---|
| CLIProxyAPI 版本 | `7.2.75` | 【實測】`strings <binary> \| grep -oE '^7\.[0-9]+\.[0-9]+$'` |
| Endpoint | `http://127.0.0.1:8317` | 【實測】`/v1/models` 回 200 |
| 模型總數 | 33 | 【實測】`/v1/models \| jq '.data \| length'` |
| Google 憑證 | **只有一個**：`antigravity-qwe70301@gmail.com.json` | 【實測】`ls ~/.cli-proxy-api/*.json` |
| `gemini-api-key` | `null`（沒接 AI Studio key）| 【實測】management `/v0/management/config` |
| `vertex-api-key` | `null` | 【實測】同上 |

**推論（強）**：relay 上所有 `gemini-*` 模型**全部**經由 Antigravity OAuth 腿，沒有第二條 Google 路。keeper DB 的 `provider` 欄位對 51 筆今日 gemini 請求全部標 `antigravity`，交叉驗證成立。

### 1.2 有沒有 gemini flash？有，8 顆

`/v1/models` 列出的 flash 系（【實測】）：

```
gemini-3-flash            gemini-3.5-flash-extra-low
gemini-3-flash-agent      gemini-3.6-flash-high
gemini-3.1-flash-image    gemini-3.7-flash-high
gemini-3.1-flash-lite     gemini-3.5-flash-low
```

⚠️ **ID 與實際檔位有系統性錯位**——這是上游把舊 ID 重映射到新模型造成的。以 `displayName` 為準（【實測】今日重跑 `cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels`，確認 repo doc `docs/antigravity-account-models-2026-07-05.md` 的對照表仍成立，並補到 3.6 系）：

| relay ID | 實際是 | maxOutputTokens |
|---|---|---|
| `gemini-3.1-flash-lite` | Gemini 3.1 Flash Lite | 65535 |
| `gemini-3.5-flash-extra-low` | Gemini 3.5 Flash (**Low**) | 65536 |
| `gemini-3.5-flash-low` | Gemini 3.5 Flash (**Medium**) | 65536 |
| `gemini-3-flash-agent` | Gemini **3.5** Flash (High) | 65536 |
| `gemini-3-flash` | Gemini 3 Flash | 65536 |
| `gemini-3.6-flash-high` | Gemini 3.6 Flash (High) | 65536 |
| `gemini-3.6-flash-medium` / `-low` | Gemini 3.6 Flash (Medium / Low) | 65536 |

另外帳號側還有 relay 清單裡沒有的 `gemini-3.6-flash-tiered`、`gemini-3.6-flash-medium`、`gemini-3.6-flash-low`（【實測】fetchAvailableModels 回應含這三顆，relay `/v1/models` 不含）。`defaultAgentModelId` = `gemini-3.6-flash-high`。

### 1.3 最小判讀請求實測（7 顆 flash 各一發）

Prompt：繁中限時優惠判讀 + JSON 輸出，`max_tokens=200`、`temperature=0`。**7/7 全 HTTP 200，7/7 判讀正確。**

| 模型 | 延遲 | prompt | completion | **其中 reasoning** |
|---|---|---|---|---|
| `gemini-3.1-flash-lite` | **3.86s** | 106 | 40 | **0** |
| `gemini-3.7-flash-high` | 5.54s | 106 | 63 | 283 |
| `gemini-3-flash-agent` | 6.01s | 106 | 41 | 897 |
| `gemini-3.6-flash-high` | 6.54s | 106 | 61 | 725 |
| `gemini-3.5-flash-low` | 6.11s | 106 | 42 | 684 |
| `gemini-3.5-flash-extra-low` | 6.74s | 106 | 44 | 749 |
| `gemini-3-flash` | 23.31s | 106 | 38 | 741 |

> **本輪最重要的發現：thinking flash 的隱藏 reasoning token 是可見輸出的 6–24 倍。**
> `gemini-3-flash` 為了吐 38 個字的答案，燒了 741 個 reasoning token（總計 885）。
> `gemini-3.1-flash-lite` 是唯一 **reasoning = 0** 的一顆：總計 146 token 就把同一題做對。**差 6 倍。**
> 這直接推翻票 002 的 400 in / 60 out 估算基礎——見子問 5 的成本重算。

### 1.4 真實一輪負載實測（10 筆並發，模擬每小時一輪）

10 篇貼文（5 篇真優惠、5 篇雜訊）餵 `gemini-3.1-flash-lite`，全並發：

```
WALL_CLOCK_FOR_10_CONCURRENT = 2.91s
OK=10  FAIL=0
ROUND TOTALS: prompt=835  completion=348  (reasoning=0)
PER-CALL AVG: in=83  out=34
```

**`is_deal` 分類 10/10 正確**（5 個 `true` 對應 5 篇真優惠，5 個 `false` 對應生活貼文 / 徵才 / 颱風公告 / 開箱心得 / 新品通知）。

⚠️ **但「分類正確」不等於「欄位正確」**——同一批裡有 2 筆 `deadline` 的**年份是 2024**（`"2024-08-19 23:59"`、`"2024-08-31"`）。分類對、日期錯。詳見 1.5。

延遲分布（keeper DB `latency_ms`，今日 n=31）：

```
min=1856  p50=2902  p90=3154  p95=3422  max=3857  mean=2861   (ms)
ttft: min=564  p50=809  max=1394 (ms)
```

**每小時一輪、每輪 <10 條的需求，用掉 2.9 秒。餘裕約 1200 倍。**

### 1.5 ⚠️ 實測抓到的品質雷：flash-lite 會憑空編年份

貼文只寫「只到 8/19 晚上 11:59」（沒寫年份）時：

| 條件 | 3 次輸出 |
|---|---|
| **系統提示沒給今天日期** | `"deadline":"2024-08-19T23:59:00"` ×3 — **年份錯 2 年** |
| **系統提示注入 `今天是 2026-08-17`** | `"deadline":"2026-08-19T23:59:00"` ×3 — 正確 |

`temperature=0` 下 3/3 穩定復現，兩組都是確定性的。

**行動項（P0）**：判讀 prompt **必須注入當前日期與時區**。這對「限時優惠盯梢」是致命 bug——deadline 算錯年份 = 整條產線的排序與過期判斷全錯，而且不會報錯、只會靜靜給你 2024 年的資料。

### 1.6 429 / 失敗率

今日 antigravity 腿累計（【實測】keeper DB）：

```
total_calls=51  failures=0  tin=7148  tout=7801  treason=6633
```

**51 發 0 失敗、0 個 429。** relay log 裡 14:22–14:40 那批 429 全部屬於 nvidia / opencode-zen 免費池（keeper `usage_identities`：nvidia failure_count=8、opencode-zen failure_count=17，antigravity failure_count=0），與本腿無關。

CPA binary 內建的 antigravity 錯誤處理（【實測】binary strings）：

```
antigravity executor: soft rate limit for model %s, retrying in %s (attempt %d/%d)
antigravity executor: transient 429 resource exhausted for model %s, retrying in %s (attempt %d/%d)
antigravity executor: no capacity for model %s, retrying in %s (attempt %d/%d)
antigravity executor: short quota cooldown (%s) for model %s recorded
antigravity executor: rate limited on base url %s, retrying with fallback base url: %s
```

→ **relay 已自動吸收 transient 429**（重試 + 換 fallback base URL `daily-cloudcode-pa.googleapis.com` + 短冷卻），`request-retry: 3`。這是好消息，但也意味著**你的 script 看到的 429 已經是 relay 重試失敗後的殘餘**，不能當第一線訊號。

### 1.7 上游端點（決定性事實）

【實測】binary strings 抓到的 Google 端點：

```
https://cloudcode-pa.googleapis.com          ← Antigravity 主力
https://daily-cloudcode-pa.googleapis.com    ← fallback
https://generativelanguage.googleapis.com    ← 公開 Gemini API（本腿沒用到）
```

OAuth scope（【實測】`oauth2/v3/tokeninfo`）：
`email profile openid cloud-platform experimentsandconfigs cclog userinfo.email userinfo.profile`

> **這條腿打的是 `cloudcode-pa.googleapis.com`（Google Code Assist 的內部 API），不是公開的 `generativelanguage.googleapis.com`。**
> 推論（強）：**公開 Gemini API 的 RPM/RPD 文件數字不管這條腿。** 子問 2 要分兩套看。

---

## 子問 2｜AI Pro 訂閱的 Gemini API／CLI 額度語意

### 2.1 【實測】決定性證據：這個帳號在 Google 眼中是 `free-tier`

直接拿 `~/.cli-proxy-api/antigravity-qwe70301@gmail.com.json` 的 `access_token` 打
`POST https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist`（HTTP 200，3923 bytes）：

```json
{
  "currentTier": {
    "id": "free-tier",
    "name": "Antigravity",
    "upgradeSubscriptionText": "Upgrade to get 1,500 model requests per day with Gemini CLI and Gemini Code Assist's agent mode with Google AI Pro.",
    "upgradeSubscriptionType": "GOOGLE_ONE"
  },
  "allowedTiers": [
    { "id": "free-tier", "name": "Antigravity", "isDefault": true },
    { "id": "standard-tier", "userDefinedCloudaicompanionProject": true, "usesGcpTos": true }
  ],
  "cloudaicompanionProject": "innate-entity-1ttt2",
  "gcpManaged": false,
  "paidTier": {
    "id": "g1-pro-tier",
    "name": "Google AI Pro",
    "upgradeSubscriptionText": "You can upgrade to a Google AI Ultra plan to receive higher rate limits.",
    "availableCredits": [
      { "creditType": "GOOGLE_ONE_AI", "minimumCreditAmountForUsage": "50" }
    ]
  }
}
```

**三個相互矛盾的訊號，必須攤明而不是硬圓：**

1. `currentTier.id` = **`"free-tier"`** — 這次 session 被當免費層服務。
2. `paidTier.name` = **`"Google AI Pro"`** — Google **確實認得**這個帳號的 AI Pro 權益。
3. `currentTier` 同時掛著**升級 AI Pro 的推銷文案**——對一個已經是 AI Pro 的帳號顯示升級 CTA，說明後端在這條路徑上**沒有把 session 認成 AI Pro-entitled**。

**最可能的讀法（標為推測、無法從單一回應證實）**：`currentTier` 是「這個 project 這條路徑當前服務層級」，`paidTier` 是「帳號持有的訂閱權益」；AI Pro 權益走的是 `GOOGLE_ONE_AI` credit 機制（`minimumCreditAmountForUsage: 50`）而不是切換 `currentTier`。credits 餘額不足 50 時就落回 free-tier。**這條推測我無法證實**——CPA 內部有 credits 追蹤（binary 有 `antigravity_credits.go`、`antigravityCreditsBalance`、KV prefix `cpa:antigravity:credits-balance`），但 7.2.75 **沒有把餘額暴露出來**：`GET /v0/management/antigravity-credits` 回 404（今日與 2026-08-07 各試一次，log 都留了 404）。

**對規劃的實際意涵**：不要把這條腿當「我付錢的 AI Pro 額度」規劃。它現在拿到的是 free-tier 服務，而 free-tier 的權益 Google 隨時可以改，改的時候不會通知你。

### 2.2 ⚠️ 大翻案：問題的前提在 2026-06-18 就失效了

我原本要引用 `loadCodeAssist` 回應裡那句「1,500 requests/day with Gemini CLI ... with Google AI Pro」當 AI Pro 的額度數字。**那是過期文案。**

【文件】Google 官方公告（文章日 2026-05-19、抓取日 2026-08-17、HTTP 200）
<https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/>

> "On June 18, 2026, **Gemini CLI and Gemini Code Assist IDE extensions will stop serving requests for Google AI Pro and Ultra**, as well as those using it free of charge using Gemini Code Assist for individuals."

**今天是 2026-08-17，那個日期過去兩個月了。AI Pro 的開發者路徑已經正式改成 Antigravity CLI。**

企業層不受影響（同文）：

> "If your organization uses Gemini CLI or our IDE extensions via a Gemini Code Assist Standard or Enterprise license... your access remains unchanged."

Cloud 官方額度頁已經同步下架消費者列——<https://docs.cloud.google.com/gemini/docs/quotas>（HTTP 200、`Last updated 2026-08-11 UTC`）的 "Quotas for agent mode and Gemini CLI" 表**只剩兩列**（Standard 1500 / Enterprise 2000），`"AI Pro"` / `"AI Ultra"` / `"Google One"` / `"individual"` 出現次數**全部 0**。

> ⚠️ **1,500/day 這個數字仍然掛在多處官方頁面上**（`google-gemini/gemini-cli` repo 的 `docs/resources/quota-and-pricing.md`，內容最後實質更新 2026-03-26；geminicli.com 同一頁甚至**頂部橫幅寫著「Google One users: Gemini CLI will be replaced by Antigravity CLI on June 18th」、第 223 行照舊列 `| Google AI Pro | 1,500 requests |`**）。
> **任何人單看那張表都會被誤導。** 我自己第一輪就差點引用它。

### 2.3 這解釋了 2.1 的謎團（但別誤讀成「所以 relay 沒問題」）

**Antigravity 這個「產品」就是 Google 現在指定給 AI Pro 的開發者路徑**，不是灰色的替代品。

> ⚠️ **必須切開兩層，否則會得出完全錯的結論：**
> | 問題 | 答案 |
> |---|---|
> | AI Pro 的開發者額度該走 Antigravity 嗎？ | **✅ 是**，官方指定（本節） |
> | 可以把 Antigravity 的 OAuth 憑證接到 CLIProxyAPI 給 cron 用嗎？ | **⛔ 不行**，Antigravity ToS §6 明文違約、且有實際封號紀錄（見 3.3 / 3.4） |
>
> **第 1 層的正當性不會延伸到第 2 層。** Google 自己在 FAQ 裡把這兩件事講得很清楚：要用第三方 agent 就「use a Vertex or AI Studio API key」，不要接 Antigravity login。

【文件】<https://antigravity.google/docs/plans>（HTTP 200、抓取日 2026-08-17、版本戳 Antigravity 2.0 v2.8.1 / CLI v1.1.13）：

> "Users on Google AI Pro receive:
> - **High, generous quota, refreshed every five hours until weekly limit reached**
> - Higher weekly rate limit
>
> Users not on AI Pro and Ultra plans receive:
> - Meaningful quota, refreshed weekly
> - Weekly rate limit"

Google 明講為什麼不給數字：

> "The baseline rate limits are primarily determined to the degree we have capacity, and exist to prevent abuse. Under the hood, **the rate limits are correlated with the amount of work done by the agent**, which can differ from prompt to prompt."

> "Usage limits for this service are subject to modification."

**這條和我的實測對上了（子問 4.2）**：我探到所有 Gemini 系共用一個 `resetTime`（2026-08-17T07:54:15Z，當時約 40 分鐘後），而 Claude 系另一組（11:18:55Z）。**「every five hours」的滾動視窗**正好解釋這個形狀，也解釋為什麼 240 req/day 完全撼動不了 `remainingFraction`——那個池子是按「IDE agent 的工作量」而不是「請求數」定尺寸的。

**額度不是這條腿的瓶頸。** 你的 10 條小判讀 vs 一次 agentic coding session 的工作量，差好幾個數量級。

⚠️ 但 `currentTier: free-tier` 仍然沒被解釋掉。Antigravity 對 Free / AI Pro / Ultra 是**分級的額度**，而我探到的 `currentTier` 是 `free-tier`。**relay 這條腿到底吃到 AI Pro 級還是 Free 級的 Antigravity 額度，未驗證。** 好消息是兩者對 240 req/day 都夠（Free 也有 "Meaningful quota, refreshed weekly"）。

Antigravity 的模型可用性（【文件】<https://antigravity.google/docs/models/>）也對上我的實測清單：Gemini 3.7/3.6/3.5 Flash + 3.1 Pro 對所有消費者層 ✅；Claude Sonnet 4.6 / Opus 4.6 / GPT-OSS-120b 對 Free / Plus / Pro / Ultra ✅、Enterprise ❌。**注意 Claude 系對 Free 層也是 ✅，所以「relay 上能用 Claude」不能反證帳號吃到 AI Pro 級額度。**

另外兩條值得知道：
- Antigravity 用量面板只分**兩池**：`Gemini Models` 與 `Claude and GPT models`，各有 Weekly / Five-Hour 兩個計數。**Gemini 池內 flash 與 pro 共用額度、沒有 flash/pro 分池**——所以主力用 flash 不會「省下 pro 的配額」。
- Antigravity 明文：**"There is currently no support for: Bring-your-own-key or bring-your-own-endpoint for additional rate limits"**。

### 2.4 ⚠️ 最該立刻檢查的一件事：`antigravity-credits` 是開著的

【文件】<https://antigravity.google/docs/plans>：

> "Users on Google AI Pro or Ultra plans can utilize **purchased AI credits** (or any one-time promotional credits) for additional overage usage above the baseline provided quota. **AI credits are consumed at standard Gemini Enterprise Agent Platform consumption pricing.**"

> "Usage of credits once the baseline quota is exhausted ... is controlled by the "AI Credit Overages" user setting:
> - **Never**: Never use AI credits automatically, wait until the baseline quota refreshes
> - **Always**: Always use AI credits when the baseline quota is exhausted"

**你的 relay 現在設的是「Always」的等效值。**【實測】live config：

```json
"quota-exceeded": { "switch-project": true, "switch-preview-model": true, "antigravity-credits": true }
```

而我的 `loadCodeAssist` 探測回應裡有：

```json
"availableCredits": [ { "creditType": "GOOGLE_ONE_AI", "minimumCreditAmountForUsage": "50" } ]
```

CPA binary 也確實有 credits 消費路徑（`antigravity_credits.go`、`antigravityCreditsBalance`、`shouldAttemptAntigravityCreditsFallback`、KV prefix `cpa:antigravity:credits-balance`）。

> ⚠️ **這是一個無人值守 24/7 排程最不該有的設定組合：額度用完 → 自動改花真錢買的 credits → 沒有任何通知。**
> **我試了四條路都查不到餘額**（【實測】）：
> - `GET /v0/management/antigravity-credits` → 404（今日與 2026-08-07 各一次，log 都留了 404 紀錄）
> - `loadCodeAssist` 回應裡對 `credit` 做全路徑掃描 → **只有 `creditType: "GOOGLE_ONE_AI"` 與 `minimumCreditAmountForUsage: "50"` 兩個欄位，沒有任何餘額數字**
> - 上游試 `v1internal:{getCredits,fetchCredits,loadCredits,getUserCredits}` → 全 404
> - CPA binary 只實作 5 個 `v1internal:` 端點（`loadCodeAssist` / `fetchAvailableModels` / `generateContent` / `streamGenerateContent` / `countTokens`），沒有 credits 查詢
>
> 所以精確的說法是：**relay 側的自動付費機制是「上膛」狀態，但油箱裡有沒有油看不到。** `minimumCreditAmountForUsage: 50` 的單位（次數？美元？）也未知。
> **行動項（P1）**：接 deal-watch 之前，去 Antigravity 設定把 "AI Credit Overages" 設成 **Never**，或把 relay config 的 `quota-exceeded.antigravity-credits` 改 `false`（熱重載）。理由：你要的是「額度用完就降級到備援」，不是「額度用完就開始花錢」。

你的需求 = 10 × 24 = **240 requests/day**。以任何合理的池子尺寸看都是零壓力，但上面這條的風險不在量、在**沒有上限的自動付費行為**。

### 2.5 ⚠️ free-tier 的隱私條款（這條比額度重要）

同一個回應裡的 `privacyNotice.noticeText`（`showNotice: true`，即尚未 opt out）：

> "When you use Gemini Code Assist for individuals, Google collects **your prompts, related code, generated output**, code edits, related feature usage information, and your feedback to provide, improve, and develop Google products and services and machine learning technologies.
>
> To help with quality and improve our products (such as generative machine-learning models), **human reviewers may read, annotate, and process the data collected above.** ... storing those disconnected copies for **up to 18 months**. Please don't submit confidential information or any data you wouldn't want a reviewer to see."

**對 deal-watch 的實際風險：低但要知道。** 你餵進去的是公開社群貼文（本來就公開），輸出是 JSON 判讀結果。沒有機密資料。但如果之後這條產線被拿去判讀任何私人內容（私訊、內部價格表、客戶名單），這段條款就變成 blocker。

### 2.6 公開 Gemini API（另一條路，本機已有現成 key）

repo 內 `docs/gemini-free-key-probe-2026-07-05.md` 記錄（【文件】，43 天前實測、本輪未重驗）：

- 主力 AI Studio key 在 cursor MCP config（`GOOGLE_API_KEY`），**免費層**（429 的 `quotaId` 帶 `-FreeTier` 後綴實證）
- flash 家族可用（`gemini-3.5-flash` / `gemini-3-flash-preview` / `gemini-2.5-flash` 都 200）；**pro 系免費層配額 = 0**
- 「官方已不公開靜態數字表」；第三方快照 flash 系約 **10–15 RPM / 數百–1500 RPD**（volatile、僅供量級參考）
- **配額是 per-Google-Cloud-project、不是 per-key**
- ⚠️ **開帳單 = 免費層立即消失**（該 project 所有呼叫從第一個 token 計費）

**2026-08-17 官方文件重驗結果**（【文件】<https://ai.google.dev/gemini-api/docs/rate-limits>、HTTP 200、`Last updated 2026-08-13 UTC`）：

repo doc 那句「官方已不公開靜態數字表」**經本輪查證確認仍然成立，而且是刻意的**：

> "Rate limits depend on a variety of factors (such as your usage tier) and can be viewed in **Google AI Studio**."
> "Each model variation has an associated rate limit (requests per minute, RPM). For details on those rate limits, see the **AI Studio Rate Limit page**."
> "Specified rate limits are not guaranteed and actual capacity may vary."

枚舉否證（對原始 HTML 數，不是肉眼掃）：該頁 `<table>` 共 5 張，全部點名得出來——spend-based 限制 ×1、usage tier 資格 ×1、Batch enqueued tokens Tier 1/2/3 ×3。**沒有一張是每模型 RPM/RPD 表。** `"Requests per minute"` 出現 1 次（只在概念說明段）、`"Requests per day"` 2 次。

→ **free tier 與 Tier 1/2/3 的具體 flash RPM/RPD/TPM 數字，2026-08 當下不在任何免登入的 Google 頁面上。** 權威來源只有 `aistudio.google.com/rate-limit`（需登入，本輪抓不到）。第三方的 10–15 RPM 只能當量級參考。

當期 flash 模型 ID（【文件】pricing 頁、`Last updated 2026-08-13 UTC`）：`gemini-3.7-flash`（最新、$0.75/MTok 到 2026-12-31，之後 $1.50）、`gemini-3.6-flash`、`gemini-3.5-flash`、`gemini-3.5-flash-lite`、`gemini-3.1-flash-lite`、`gemini-3-flash-preview`。

**這條路的意義**：它是一條**獨立的配額池**（不同 project、不同端點、**有正式公開條款**），可以當備援鏈的一環，而且不用再開任何帳號。即使按最保守的第三方數字 10 RPM，對 stagger 後的「每小時 10 條」也夠（見子問 5）。

> ⚠️ **時效**：key 本身的狀態為 2026-07-05 的實測快照，**本輪未重驗**。真要接進備援鏈之前應重跑一次 `GET /v1beta/models?key=...` 與一發 `generateContent`。

### 2.7 未能驗證的部分（誠實標註）

- **AI Pro 是否提升公開 Gemini API key 的 RPM/RPD**：本輪已查官方文件，判定為**「沒有任何官方文件說會」**——但這是**缺席論證**，不是正面陳述。證據：`rate-limits` / `pricing` / `terms` 三頁對 `"Google One"` / `"AI Pro"` / `"subscription"` 的出現次數**全部 0**；四階 tier 資格條件（Free / Tier 1 $10 / Tier 2 $200+$100 已付 / Tier 3 $200+$1000 已付）**只認 Cloud Billing 付款紀錄**。條款也把兩者定義成不同 Service：
  > "Your access to **Gemini API** is a "Paid Service" **only when** accessing the API through a Cloud Project associated with an active billing account."
  信心：中高。弱點是找不到 Google 正面寫「AI Pro 不提高 API 速率」的句子。
- ⚠️ **但這個假設有一半被推翻**：<https://one.google.com/about/google-ai-plans/> 明文把 **AI Studio** 列為 AI Pro 權益（繁中原文：「在 AI Studio、Google Antigravity 和 Jules 享有更寬裕的用量額度」；註腳 5：「使用 Google AI Studio 時，須遵守《Gemini API 附加服務條款》」）。所以「AI Pro 跟 Gemini API 生態完全無關」是**錯的**——它提高 AI Studio **互動式網頁介面**的額度，只是沒證據說會提高你程式化打 API key 的 RPM。
- ⚠️ **兩個 Google 官方消費者頁面互相不一致**：one.google.com 把 AI Studio 列入 AI Pro 權益；<https://gemini.google/us/subscriptions/> 的 AI Pro 權益清單裡 `"AI Studio"` / `"API"` / `"Gemini CLI"` / `"Code Assist"` 出現次數**全部 0**。同一家公司對同一個方案給出不同權益清單。
- **relay 這條腿吃到 AI Pro 級還是 Free 級的 Antigravity 額度**——`currentTier: free-tier` vs `paidTier: Google AI Pro` 的矛盾未解。
- `GOOGLE_ONE_AI` credit 的實際餘額、消耗率、以及 `minimumCreditAmountForUsage: 50` 的單位（次數？美元？）——**全部未知**，relay 沒暴露（404）、API 回應沒給數字。
- **Antigravity 的 AI Pro 具體額度數字**——Google 刻意不發布（"subject to modification"）。
- **「每模型 RPM 表格曾經存在後來被移除」的時序**——web.archive.org 本輪全掛（`archive.org/wayback/available` 回 502、`web.archive.org/web/*` 回 503），只能證明**現在沒有**，不能證明**曾經有**。

---

## 子問 3｜無人值守排程的 ToS

### 3.1 兩條 transport 的結論先講

| | (A) **付費** Gemini API key | (A') 免費 Gemini API key | (B) Antigravity OAuth 經 relay |
|---|---|---|---|
| 端點 | `generativelanguage.googleapis.com`（公開、有文件） | 同左 | `cloudcode-pa.googleapis.com`（**內部 `v1internal`**） |
| 條款狀態 | **✅ 允許，Google 主動推薦** | ⚠️ 允許，但 "not for consumer use" 字面不利 | **⛔ 明文違約**（Antigravity ToS §6） |
| 每日額度餘裕 | 充足 | **240/250 = 96%，數學上不可行** | 240/1500 = 16% |
| 你的資料被訓練 | **否** | **是，含人工審閱** | Interactions 用於改進產品 |
| 已知執法紀錄 | 無 | 無 | **2026-02～05 實際封號潮；二犯永久** |
| 月成本（實測 token 量） | **$0.16–0.79** | $0 | $0（但你已在付 AI Pro） |

> **量級不是任何一條路的瓶頸**（詳見 3.5：尖峰 10 RPM、duty cycle 1.7%）。差別純粹在條款與資料處理。

### 3.2 排程本身完全合法（路徑 A：Gemini API key）

**判定：找不到任何禁止 automation / unattended / cron / 排程的條款。**

【文件】三份全文讀過（不是抓關鍵字）：
- <https://ai.google.dev/gemini-api/terms>（`Effective March 23, 2026` / `Last updated 2026-04-28 UTC`）——`automat` 只出現在 Grounding with Google Search 段（講不准程式化收集 Links），與一般 API 呼叫無關。
- <https://developers.google.com/terms>（Google APIs ToS，`Last modified: November 9, 2021`）——§5a "API Prohibitions" 7 條逐條讀完，**沒有一條提到 automation / 排程 / 無人值守**。§2c 反而正面授權程式化存取。
- <https://policies.google.com/terms/generative-ai/use-policy>（`Last Modified: December 17, 2024`）——純內容政策（CSAM / 極端主義 / 詐騙…）。

唯一沾到「無人在場」的是 Gemini API terms 的 Agentic Services 段：

> "You will not **automatically bypass any requests for human confirmation**."

只管「不要自動點掉 human confirmation 提示」。文字分類沒有 confirmation prompt，不觸發。

**額外正面證據**：Google 官方 Antigravity Python SDK 內建週期性觸發器 `every(60, check_status)`（每 60 秒），比你的頻率高 60 倍。Google 自家 SDK 出貨排程原語，把「automation 本身違規」這個假設打死。

⚠️ **兩件路徑 A 你可能沒預期的事：**

1. **「not for consumer use」條款**（Gemini API terms, Use Restrictions 第一句）：
   > "Use of Google AI Studio and Gemini API is for developers building with Google AI models for **professional or business purposes, not for consumer use**."

   這條 **2026-12-18 才加入**（archive 逐版驗證：09-25 / 10-07 / 10-17 / 11-20 命中 0 次，12-18 命中 1 次）。兩種讀法都成立：嚴格讀，「幫自己看購物優惠」是消費者用途；寬鬆讀，你是 developer 在 build 工具鏈。**INFERENCE**：這條實際上不可執法——Google 整套 abuse 執法機制都在掃內容違規，沒有任何一環在判斷「用途是商業還是消費」。紙上風險、非行為風險。

2. **免費層拿你的資料去訓練**（Gemini API terms, Unpaid Services）：
   > "human reviewers may read, annotate, and process your API input and output... **Do not submit sensitive, confidential, or personal information to the Unpaid Services.**"

   付費層相反：`"Google doesn't use your prompts... or responses to improve our products"`。定價頁每個模型列一行 Free Tier「Used to improve our products: **Yes**」／Paid Tier「**No**」。
   **對 deal-watch 的實際意涵**：抓回來的頁面可能夾帶你關注什麼商品、什麼價位。走免費層等於交出去給人工審閱。

### 3.3 ⛔ 路徑 B（Antigravity OAuth 經 relay）被逐字禁止

這不是灰色地帶。**我上一輪把它判成「灰色、務實可接受」是錯的**，以下是修正。

**（a）Antigravity 條款直接點名**——【文件】<https://antigravity.google/terms>（HTTP 200，全文 8 條逐條讀過；⚠️ 抓這頁必須 `curl --compressed`，否則拿到 brotli 二進位會誤判成「JS 渲染抓不到」）第 6 條：

> "You must not abuse, harm, interfere with, or disrupt the Service. This includes, but is not limited to, **using the Service in connection with products not provided by us. Using third party software, tools, or services to access the Service (e.g. using OpenClaw with Antigravity OAuth) is a breach of this Agreement. Such actions may be grounds for suspension or termination of your account.**"

**（b）Antigravity FAQ 用問句再講一次**——<https://antigravity.google/docs/faq>：

> "Why can't I use third party software (e.g. **Claude Code**, OpenClaw, OpenCode) with my Antigravity login?
> Using third party software, tools, or services to access Antigravity is a **violation of our Terms of Service**... Such actions may be grounds for suspension or termination of your account. **If you would like to use a third party coding agent with Gemini, we recommend using a Vertex or AI Studio API key.**"

最後一句就是 Google 自己開的正門。

**（c）Google APIs ToS §2c 三句話，relay 三句全犯**——<https://developers.google.com/terms>：

> "You will only access (or attempt to access) an API **by the means described in the documentation of that API**. If Google assigns you developer credentials (e.g. client IDs), **you must use them with the applicable APIs**. **You will not misrepresent or mask either your identity or your API Client's identity** when using the APIs."

技術對位（【文件】原始碼常數，非推測）：

| | 值 | 來源 |
|---|---|---|
| CLIProxyAPI Antigravity endpoint | `https://cloudcode-pa.googleapis.com` / `v1internal` | `internal/auth/antigravity/constants.go` |
| Google 自家 gemini-cli endpoint | `https://cloudcode-pa.googleapis.com` / `v1internal` | `packages/core/src/code_assist/server.ts:73-74` |

**同一個 host、同一個 `v1internal` 版本面。** `v1internal` 這個名字本身就說明它不是公開文件化的 API 面，所以 §2c 的 "by the means described in the documentation" 結構上不可能滿足。

而 relay **主動偽裝身分**（`internal/misc/antigravity_version.go`）：偽造 `antigravity/cli/<version>` UA、偽造 `google-api-nodejs-client/10.3.0` 與 `gl-node/22.21.1`、**每 6 小時輪詢 Google 自己的 Antigravity 自動更新 manifest 只為了讓偽造的版本號跟真客戶端同步**。這正對上 §2c 的 "misrepresent or mask... your API Client's identity"，以及 Google Universal Terms（`Effective July 30, 2026`）的 `"bypassing our systems or protective measures"` 與 `"hiding or misrepresenting who you are"`。

**（d）Google 自家 gemini-cli 文件同句適用**——`docs/resources/tos-privacy.md`：

> "**Directly accessing the services powering Gemini CLI** (for example, the Gemini Code Assist service) **using third-party software, tools, or services** (for example, using OpenClaw with Gemini CLI OAuth) **is a violation of applicable terms and policies.**"

⚠️ **修正我上一輪的一個反向論點**：我曾拿 Antigravity 文件那句 "There is currently no support for: Bring-your-own-key or bring-your-own-endpoint" 推論「Google 的態度是不保證而非違規」。**那個推論站不住**——同一份文件體系裡有明文的 "is a breach of this Agreement"，措辭層級完全不同。

**Google One / AI Pro 訂閱條款本身不是問題來源**（<https://one.google.com/terms-of-service>，`Last Modified: November 11, 2025`，⚠️ 此頁未親驗）：沒有 automated-access 或 reverse-engineering 條款，最接近的是禁止轉讓 AI credits（你自用不觸發）。**問題來源是 Antigravity 的 Additional Terms。**

### 3.4 ⛔ 執法現實：這是少數紙上禁令真被執行的案例

**（a）Google 官方承認執法過。** gemini-cli discussion #20632「Addressing Antigravity Bans & Reinstating Access」（2026-02-27，77 則留言），作者 `jackwotherspoon` = Google DevRel（`gh api users/jackwotherspoon` → `company: "@google"`, `bio: "❇️ Gemini CLI DevRel"`）：

> "These were the result of a series of **Antigravity bans** rolled out to address violations of the Antigravity Terms of Service, specifically **the use of 3rd party tools or proxies to access Antigravity resources and quotas.**"
>
> "Because of the backend layer where abuse prevention occurs, **bans for Antigravity usage also blocked access to Gemini CLI and Gemini Code Assist.**"
>
> "**Permanent ban: If an account is flagged for a second violation of ToS, it will be permanently banned.**"

**（b）實際回應形狀**（CLIProxyAPI issue #1823, 2026-03-04，標題就是 "Appeal Form — Terms of Service suspension for Antigravity, Gemini CLI, and Gemini Code Assist"）：

```
403 PERMISSION_DENIED
"This service has been disabled in this account for violation of Terms of Service"
reason: "TOS_VIOLATION"
domain: "cloudcode-pa.googleapis.com"     ← 正是這條腿打的 host
metadata: { appeal_url: "https://forms.gle/..." }
```

Google 甚至把這狀態做成一級產品功能：gemini-cli 有專屬 `AccountSuspendedError` 型別與 `BannedAccountDialog.tsx`，於 commit `ea48bd94`（2026-02-27，PR #20577）引入——**跟官方公告同一天**。（此項未親驗檔案，但公告本體與 403 payload 已驗）

**（c）觀察到的影響範圍：服務層，不是整個 Google 帳號。** 四筆第一手報告（其中僅 #1823 親驗，餘未親驗）：

- CLIProxyAPI#1637：「**Email is working but** can not use the pro service of gemini or Antigravity」
- gemini-cli#25685：「Consumer Gemini at gemini.google.com: working normally. Gemini API via AI Studio / Vertex keys: working normally. **Only the CLI OAuth sign-in is blocked.**」
- gemini-cli#25609：「I can successfully use Gemini Pro on the web with the same Google account, **so the account itself is active and not suspended.**」
- gemini-cli#27199：「I was able to sign in through the browser, but the CLI still shows this error」

搜「整帳號終止」全 0 命中（`"account terminated"` / `"lost access to gmail"` / `"whole google account"` / `"google account deleted"` in `org:google-gemini` 皆 `total_count 0`）。

> ⚠️ **但這是「觀察到的範圍」，不是「保證的上限」。** Google Universal Terms 保留 `"delete your Google Account"` 的權利；discussion #20632 裡有人直接問「Do you still ban the whole Google account (Gmail, etc)?」——**Google 方沒有回答**。那是未回應，不是否認。

**最壞情況排序（依證據強度）：**

| 後果 | 證據等級 |
|---|---|
| 服務層 403 停權（Antigravity + Gemini CLI + Code Assist 一起死） | **已證實** — 官方公告 + 完整 payload + 多筆一手報告 |
| refresh token 撤銷 → 需重跑 `--antigravity-login` | 已證實 |
| 申訴後 1–2 天恢復（首犯可逆） | 官方明文承諾 |
| **二犯永久封禁** | **官方政策明文**，但查不到實際永久封禁的驗證案例 |
| 整個 Google 帳號終止／刪除 | **無案例**（0/N），條款有授權但未觀察到；**Google 拒答**。INFERENCE：機率低但非零 |

**（d）反向證據（一起讀，沒有藏）：**

1. **執法窗口 2026-02～05，之後安靜。** `created:>2026-06-01` 搜 `"disabled in this account" "Terms of Service"` → `total_count 1`（且唯一那筆完全無關）；`repo:router-for-me/CLIProxyAPI` + `"violation of Terms of Service"` + `created:>2026-06-01` → `total_count 0`。**近三個月查無新的 relay 歸因封號案例。**
2. **但沒消失、只是變背景常態**：另一個 relay 專案 `diegosouzapw/OmniRoute#5600`（2026-06-30）在文件化它的「**permanent account ban detection**」功能——掃回應內容找封號關鍵字。整個 relay 生態把永久封號當日常在處理。
3. **部分 4 月的「被封」其實是 Google 的 quota 事故**（一位 Google Cloud 工程師公開說明是 resource management policy 轉換造成的 `RESOURCE_EXHAUSTED`；未親驗）。不能把該期間所有報告都算封號。
4. 官方客戶端單人單 session 也可能撞反濫用牆。403 本身不等於「你用了 relay」。

**（e）⚠️ 工具方的態度（影響你要不要繼續依賴它）**

CLIProxyAPI（47,514 stars）**README 完全沒有封號警語**（`grep -ciE "ban|suspend|terms of service|risk|disclaim"` → **0**）。同時它的 `config.example.yaml` 第 226–228 行逐字寫：

```yaml
  # Some superstitious users believe request tracking identifiers can be used
  # as evidence for TOS enforcement bans; this option only satisfies those odd concerns.
  identity-confuse: false
```

**一邊工程化地做客戶端偽裝（cloaking 預設開啟、header scrub、版本號同步）、一邊把使用者的封號擔憂稱為「superstitious」與「odd concerns」。** 至少兩位使用者要求 README 加警語，至今未採納。這是評估這個工具可信度時該納入的事實。

### 3.5 你的量級：完全不是問題（但並發要改）

| 指標 | 值 |
|---|---|
| 尖峰 RPM（10 發落在同一分鐘） | **10 RPM** |
| 全日平均 RPM | 0.167 RPM |
| Duty cycle | **1.7%**（每 60 分鐘忙 1 分鐘） |
| 尖峰 RPS（10 路並發） | **~10 RPS** ← 要改 |
| 尖峰 RPS（循序） | ~1 RPS |

**校準參照**：Google 在 Firebase AI Logic 給「每一個終端使用者」的預設限制是 **100 RPM**（<https://firebase.google.com/docs/ai-logic/faq-and-troubleshooting>）。你的尖峰 10 RPM 是 Google 認定「單一正常人類使用者」水位的**十分之一**。Prohibited Use Policy 的 "disruption to Google's infrastructure" 在任何合理解讀下都不成立。

⚠️ **唯一要改的技術點是並發，不是總量**：Cloud 額度頁（<https://docs.cloud.google.com/gemini/docs/quotas>）明列 per-user「**Requests per second: 2**」。10 路一次打出去會超過。**循序或最多 2 路並發**即安全。（⚠️ 該頁是 Cloud / Workspace 視角，不保證同一 limiter 套用在個人層——標為部分適用。但改 stagger 的成本是零：你有 3600 秒、只需要 20 秒。）

### 3.6 未能驗證（誠實標註）

- **非 GitHub 管道的封號回報**：只搜了 GitHub。Reddit `r/google_antigravity`、V2EX、linux.do 等未搜。**這是「執法強度已下降」判斷的主要缺口。**
- **實際永久封禁案例**：政策明文存在，但查不到驗證案例。
- **整帳號終止**：0 案例，但 Google 對此拒答。
- **`one.google.com/terms-of-service`**：由 subagent 抓取，我未親自重抓。
- **`BannedAccountDialog.tsx` / commit `ea48bd94`**：由 subagent 從原始碼取得，未親驗（但公告與 403 payload 已親驗）。
- **Antigravity CLI 是否支援 headless / `-p` 模式**：<https://antigravity.google/docs/cli-getting-started> curl 只拿到 1 行 JS shell，README 沒提。**如果它支援 headless，那會是「用已付費的 AI Pro 額度跑排程」唯一合規的路**——值得補查（見 5.3 附註）。

---

## 子問 4｜消耗觀測（可落地配方）

四個候選，實測結論：**兩個好用、兩個不能用。**

### 4.1 ✅ 主要配方：keeper SQLite 直讀（唯一適合自動化的路）

```bash
DB='file:/opt/homebrew/var/cpa-usage-keeper/app.db?mode=ro'   # mode=ro 必加，勿用 immutable（有 WAL）

# 今日這條腿的總量
sqlite3 -header -column "$DB" "
SELECT COUNT(*) calls, SUM(failed) failures,
       SUM(input_tokens) tin, SUM(output_tokens) tout,
       SUM(reasoning_tokens) treason, ROUND(AVG(latency_ms)) avg_ms
FROM usage_events
WHERE provider='antigravity' AND date(timestamp)=date('now','localtime');"

# 按模型拆（抓 reasoning token 爆掉的模型）
sqlite3 -header -column "$DB" "
SELECT model, COUNT(*) n, SUM(input_tokens) tin, SUM(output_tokens) tout,
       SUM(reasoning_tokens) treason, ROUND(AVG(latency_ms)) avg_ms
FROM usage_events
WHERE model LIKE 'gemini%' AND timestamp >= datetime('now','-24 hours')
GROUP BY model ORDER BY n DESC;"

# 按 client key 歸帳（deal-watch 要自己一把 key 才分得出來，見 4.5）
sqlite3 -header -column "$DB" "
SELECT api_group_key, COUNT(*) n, SUM(input_tokens+output_tokens) tokens
FROM usage_events WHERE date(timestamp)=date('now','localtime')
GROUP BY api_group_key;"
```

【實測】上述三條全跑過有輸出。`usage_events` schema 關鍵欄位：
`model, model_alias, provider, auth_type, api_group_key, timestamp, latency_ms, ttft_ms, input_tokens, output_tokens, reasoning_tokens, cached_tokens, total_tokens, failed`

**為什麼這是主要路**：durable（31MB DB + WAL）、per-request 粒度、含 `reasoning_tokens`（這是你最需要盯的欄位）、含 `api_group_key` 可歸帳、不需 auth、不會被消費掉。

### 4.2 ✅ 輔助配方：per-model 額度水位（帳號側）

```bash
AUTH=~/.cli-proxy-api/antigravity-qwe70301@gmail.com.json
TOKEN=$(jq -r .access_token "$AUTH"); PROJECT=$(jq -r .project_id "$AUTH")
curl -s -X POST https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" \
  -H 'User-Agent: antigravity/cli/1.0.13 (aidev_client; os_type=darwin; arch=arm64)' \
  -d "{\"project\": \"$PROJECT\"}" \
| jq -r '.models | to_entries[] | select(.value.quotaInfo) |
         [.key, (.value.displayName//"-"), (.value.quotaInfo.remainingFraction),
          .value.quotaInfo.resetTime] | @tsv'
```

【實測】HTTP 200、114,754 bytes、24 顆模型帶 `quotaInfo`。今日輸出（15:14）：

```
gemini-3.1-flash-lite      Gemini 3.1 Flash Lite     0.9993861   reset 2026-08-17T07:54:15Z
gemini-3.6-flash-high      Gemini 3.6 Flash (High)   0.9993861   reset 2026-08-17T07:54:15Z
gemini-3.1-pro-low         Gemini 3.1 Pro (Low)      0.9993861   reset 2026-08-17T07:54:15Z
claude-sonnet-4-6          Claude Sonnet 4.6         0.9996      reset 2026-08-17T11:18:55Z
```

> ⚠️ **重要修正：`remainingFraction` 不是即時 per-request 計量器。**
> repo doc（2026-07-05）寫「配額監控可以直接 poll 這個 API」——**這句要打折**。實測：
> - 所有 Gemini 系共用**同一個值**（0.9993861）與**同一個 resetTime**，Claude 系另一組（0.9996）→ 是 **per-provider-family 聚合**，不是 per-model。
> - **打 20 發 flash-lite 前後，值一字不變**（0.9993861 → 0.9993861，7 位小數全同）。
> - 無流量下 25 秒間隔連 poll 3 次，值不動。
>
> **可用結論**：它是**粗粒度的「離牆多遠」指示燈**，不是計量器。在你的用量規模（240 req/day）下它根本不會動——這件事本身就是答案：**餘裕大到超出這個指標的解析度**。
> **不可用結論**：別拿它做「今天用了幾 %」的帳。那個帳要用 4.1 的 SQLite。

**✅ 這個 `resetTime` 的形狀已經和官方文件對上了**（見子問 2.3）：Antigravity 官方文件寫 AI Pro / Ultra 的額度是 **"refreshed every five hours until weekly limit reached"**，Free 層是 weekly。我實測到的「所有 Gemini 系共用一個 resetTime、Claude 系另一個」正好是**兩池 × 五小時滾動視窗**的形狀——Antigravity 用量面板官方也只分 `Gemini Models` 與 `Claude and GPT models` 兩池，各有 Weekly / Five-Hour 兩個計數。

Google 同時說明了為什麼你打 20 發看不到動靜：

> "the rate limits are **correlated with the amount of work done by the agent**, which can differ from prompt to prompt"

→ 池子是按 **agentic coding 的工作量**定尺寸的。你的 10 條 tiny 判讀在這個刻度上約等於 0。

⚠️ 仍未驗證：`remainingFraction` 到底是 per-account 消耗還是 fleet capacity；以及這條腿吃的是 AI Pro 級（5h 刷新）還是 Free 級（weekly 刷新）的池子——`currentTier: free-tier` 暗示後者，但 `resetTime` 間距暗示前者。**兩個訊號打架，未解。**

### 4.3 ❌ 不能用：relay 的 `usage-queue`

```bash
curl -s -H "X-Management-Key: $CLIPROXY_MGMT_KEY" \
  "$CLIPROXY_BASE_URL/v0/management/usage-queue?count=3"   # → []
```

【實測】回空陣列。原因：**cpa-usage-keeper 持續 LPOP 把 queue 抽乾**（`REDIS_QUEUE_IDLE_INTERVAL=1s`、`REDIS_QUEUE_BATCH_SIZE=10000`）。而且這個端點是 **pop 語意、讀了就沒了**——本來就不該多方消費。**keeper 是這條 queue 的唯一 owner，你走 4.1 讀它的 DB。**

### 4.4 ❌ 不能用（自動化）：keeper 的 HTTP API

【實測】探測結果：
- SPA 對未知路徑**一律回 200 + text/html**（catch-all）→ 早期用 HTTP code 探端點的做法會產生假陽性，別這樣測。
- 真 API prefix 是 `/api/v1/`：`GET /api/v1/status` → `401 {"error":"authentication required"}`（其餘 `/api/v1/usage_*` 回 404）。
- `POST /api/v1/auth/login` 帶正確密碼 → **`403 {"error":"fetch request required"}`**；補 `Sec-Fetch-Mode` / `Origin` / `Referer` 後仍 403。
- binary 內有 `/api/usage_events`、`/api/usage_overview`、`/api/quota`、`/api/pricing` 等路由字串，但直接打全 404（prefix 不明）。

**結論**：keeper HTTP API 是**瀏覽器導向 + CSRF 防護**的（`AUTH_ENABLED=true`、session TTL 168h）。人工看儀表板走 <http://127.0.0.1:18317>；**自動化一律走 4.1 的 SQLite**。

### 4.5 ⚠️ 觀測面的兩個缺口

1. **antigravity 腿沒有額度 headroom 欄位。** keeper `usage_identities` 對 codex 身分有 `plan_type`（`pro` / `team`）+ `active_start` / `active_until` 視窗；**antigravity 那筆這三欄全空**（【實測】）。→ keeper 只能事後算「用了多少」，**算不出「還剩多少」**。剩多少只能靠 4.2 那個粗指示燈。
2. **deal-watch 要自己一把 client key 才分得出帳。** 今日 51 發裡 39 發歸 `sk-local-…`（我的探測）、6 發歸 `sk-cc-…`（Claude Code）。deal-watch 若共用既有 key，用量會跟其他流量混在一起。**行動項（P2）**：在 `~/.cli-proxy-api/config.yaml` 的 `api-keys` 加一把 `sk-dealwatch-…`（config 熱重載、不用重啟），之後 `api_group_key` 就能乾淨歸帳。

### 4.6 Google 官方 usage 頁

**未驗證。** 本輪沒去 <https://aistudio.google.com/app/apikey> 或 Google One 後台看儀表板。repo doc（2026-07-05）記載「官方已不公開靜態數字表，權威數字要登入 AI Studio 後台看儀表板」——若要走公開 API 路線，那裡是唯一權威來源。

---

## 子問 5｜備援鏈建議

### 5.1 先修正成本前提（票 002 的估算要改）

Claude Haiku 4.5 = `claude-haiku-4-5`（full ID `claude-haiku-4-5-20251001`），**$1.00 / 1M input、$5.00 / 1M output**，200K context。
【文件】來源 = `claude-api` skill 模型表，cached 2026-06-24。

7,200 calls/月（10 × 24 × 30）下：

| token 假設 | input 成本 | output 成本 | **Haiku 4.5 月成本** |
|---|---|---|---|
| 票 002 的 400 in / 60 out | $2.88 | $2.16 | **$5.04** |
| **實測 flash-lite 83 in / 34 out** | $0.60 | $1.22 | **$1.82** |
| **實測 thinking-flash 106 in / 779 (含 reasoning)** | $0.76 | $28.04 | **$28.81** |

**兩個修正：**

1. **「已估 $11/月」偏高約 2.2 倍。** 照票 002 自己的 400/60 假設算，published pricing 下是 $5.04；照實測 token 量算是 **$1.82**。（$11 這個數字我推不出來源——若它含了別的東西，請以你的原始算法為準；我只能說按 400/60 × $1/$5 算不出 $11。）
2. **⚠️ 如果 prompt 或模型讓 Haiku 開始長篇推理，成本會跳到 $28.81/月（16 倍）。** flash 的 reasoning token 實測已證明這個放大效應是真的。Haiku 4.5 走 `thinking: {type:"enabled", budget_tokens: N}`（4.5 世代仍是這個 API）——**備援路徑務必明確關掉 thinking 或給極小 budget**，否則備援比主力貴一個數量級。

**結論：備援成本不是問題（$1.8–5/月）。** 這意味著你不需要為了省錢而容忍主力不穩——備援可以設得很積極。

3. **⭐ 而且付費 Gemini Flash-Lite 比 Haiku 更便宜**——實測 token 量下 **$0.16–0.79/月**（Batch API 再砍半）。這是整份研究最重要的成本結論：**「合規」的價格是每月不到一美元**，而你原本要用「AI Pro 額度被停 + Antigravity/Gemini CLI/Code Assist 一起失效 + 二犯永久」去換它。完整階梯見 5.3。

### 5.2 relay 給你什麼、不給你什麼

【實測】binary strings + live config：

| relay 自動處理 | relay **不**處理 |
|---|---|
| transient 429 重試（`request-retry: 3`） | **跨模型 fallback**（沒有「A 掛了換 B」） |
| 換 fallback base URL（daily-cloudcode-pa） | 跨 provider fallback |
| 同模型跨憑證輪替 + 60s 冷卻 | |
| `switch-project` / `switch-preview-model` on quota-exceeded | |
| antigravity credits 追蹤（內部，未暴露） | |

錯誤字串 `All credentials for model %s are cooling down` 證實 fallback 是**憑證層、同模型內**的。
**→ 模型階梯必須由 deal-watch script 自己實作。**

### 5.3 ⭐ 建議的階梯（主推：付費 Gemini API key，不是 relay）

【文件】定價全部來自 <https://ai.google.dev/gemini-api/docs/pricing>（`Last updated 2026-08-13 UTC`）。成本用**實測 token 量（83 in / 34 out）× 7,200 calls** 算：

| 階 | 模型 / 走哪 | 月成本 | Batch API | 為什麼放這 |
|---|---|---|---|---|
| **0 ⭐** | `gemini-2.5-flash-lite`（付費 key，$0.10/$0.40） | **$0.16** | $0.08 | 最便宜。條款乾淨、資料不進訓練 |
| **0b ⭐** | `gemini-3.1-flash-lite`（付費 key，$0.25/$1.50） | **$0.52** | $0.26 | **與你實測那顆同型號**——1.3 節的 reasoning=0 / 判讀正確結論可直接套用。**建議從這裡起步** |
| **1** | `gemini-3.5-flash-lite`（付費 key，$0.30/$2.50） | **$0.79** | $0.40 | 定價頁定位「optimized for **high-volume agentic tasks**, translation, and **simple data processing**」——短文分類的靶心 |
| **2** | `claude-haiku-4-5`（thinking 關掉） | **$1.82** | — | **換 vendor**。真正的斷路保護（Google 全掛也還活著） |

**四階全部是付費、有正式條款、資料不進訓練的路。合計最壞情況月成本 < $2。**

⛔ **不建議放進階梯的兩條：**

| 排除項 | 理由 |
|---|---|
| relay / Antigravity OAuth | Antigravity ToS §6 明文違約 + 有實際執法紀錄（見 3.3 / 3.4） |
| **免費 Gemini API key** | ⚠️ **兩個獨立的硬理由**：① 官方 CLI 額度文件列免費層 **250 RPD**，你要 240 → **96%**，一次 retry 就爆；② 免費層 prompt + output **進訓練流程且有人工審閱**。**我上一輪把它排在階 2，那是錯的。**<br>⚠️ 但 250 這個數字有兩個保留：來自 **Gemini CLI 情境**的額度表（不必然等於裸 API key 的 RPD），且該檔內容最後實質更新 2026-03-26。同 repo README 另寫「1000 requests/day」，兩者互相矛盾。**保守起見當它不可用。** |

**設計要點：**

1. **0→0b→1 是同一個 project 的同一個配額池**，只保護模型級故障。真正的斷路保護是 **1→2**（換 vendor）。若要在 Google 側也有獨立池，需開第二個 Cloud project（配額是 **per-project、不是 per-key**）。
2. **不要把 429 當唯一觸發條件。** 建議觸發：`HTTP != 200` **或** `latency > 15s`（實測 p95 = 3.4s，15s 是 4.4 倍餘裕）**或** JSON parse 失敗。
3. **每階最多重試 1 次再降**（帶 exponential backoff）。
4. **降級要留痕**：每次降級寫一行 log（哪一階、什麼觸發、時間）。否則你不會知道主力已經爛了兩週、只是備援一直在扛。
5. **日期注入是所有階共用的 prompt 前綴**（1.5）。Haiku 也可能犯同樣的年份錯（未實測，但沒理由認為它免疫）。
6. **Haiku 4.5 務必關 thinking**：`thinking: {type:"enabled", budget_tokens: N}` 是 4.5 世代的 API——不設就是關的，但別誤開。開了成本跳到 **$28.81/月（16 倍）**。

> 📌 **如果你堅持要用已經付錢的 AI Pro 額度**，唯一可能合規的方向是**官方 Antigravity CLI 的 headless 模式**（用官方 binary 就不觸犯 "third party software" 條款）。⚠️ **它是否支援 headless / `-p`，本輪未能確認**（`antigravity.google/docs/cli-getting-started` curl 只回 1 行 JS shell）。這值得單獨補查——若支援，那是「AI Pro 額度 + 合規 + $0」的唯一交集。
> 另一條已失效的：官方 `gemini` CLI 的 headless 模式（Google 官方文件明文教 cron 自動化、用途列 "Batch processing" / "Tool building"）——但 Code Assist 個人版 2026-06-18 停服，這條對 AI Pro 已關閉。

### 5.4 接線前先做的事

| 優先 | 行動 | 理由 |
|---|---|---|
| **P0** | **改走付費 Gemini API key**（AI Studio 開 billing → 自動升 Tier 1，「typically take effect instantly」，billing tier cap $250、你每月 <$1 永遠碰不到） | 【文件】Antigravity ToS §6 明文違約 + 實際封號紀錄；付費路每月 $0.16–0.79。**風險換不到任何額度必要性** |
| **P0** | 判讀 prompt 注入「今天是 YYYY-MM-DD（時區）」 | 【實測】不注入 → deadline 年份錯成 2024，3/3 復現、靜默錯誤 |
| **P0** | 模型選 `gemini-3.1-flash-lite` 或 `2.5-flash-lite`，**不要**用 thinking flash | 【實測】reasoning token 差 6–24 倍 |
| **P1** | 10 發改**循序或最多 2 路並發** | 【文件】Cloud 額度頁 per-user「Requests per second: **2**」；你有 3600 秒卻只需 20 秒 |
| **P1** | 考慮 **Batch API**（成本再砍半） | 你每小時一批、不需即時回應，結構上完全適配。Tier 1 的 Batch enqueued tokens（flash-lite 10M）遠大於你的量 |
| **P2** | *若暫時仍走 relay*：**關掉 AI credits 自動 overage**（Antigravity 設定 "AI Credit Overages" → `Never`，或 relay config `quota-exceeded.antigravity-credits: false`，熱重載） | 【文件+實測】現在是「額度用完自動花購買的 credits」。無人值守 + 自動付費 + 零通知（見 2.4） |
| **P2** | *若暫時仍走 relay*：給 deal-watch 一把專屬 `sk-dealwatch-…` client key | 否則用量混在 `api_group_key` 裡分不開（熱重載） |

### 5.5 ⚠️ 一個票 002 可能要改的引用

若票 002 或先前的規劃引用了「AI Pro 給 1,500 requests/day（Gemini CLI）」——**那個數字已過期**（Gemini CLI 於 2026-06-18 停止服務 AI Pro）。正確的替代說法是：**AI Pro 的開發者額度走 Antigravity，Google 刻意不公布數字**（只說「五小時刷新一次、直到週上限」，且按 agent 工作量而非請求數計）。實務上對 240 req/day 沒有影響，但引用時別再用那個數字。

---

## 附錄：本輪確立 vs 仍未驗證

**A. 本機實測確立（有輸出可覆核）**
1. relay 上 8 顆 gemini flash 可用，7/7 判讀正確
2. `gemini-3.1-flash-lite` reasoning=0、p50 2.9s、p95 3.4s、ttft p50 809ms
3. thinking flash 隱藏 reasoning 是可見輸出的 6–24 倍
4. 10 並發 2.91s、今日 51 發 0 失敗 0 個 429
5. flash-lite 不給日期會編 2024 年；給日期就對（各 3/3、temperature=0 確定性）
6. 帳號 `currentTier = free-tier`、`paidTier = Google AI Pro`（Google 自家 `loadCodeAssist` 回應）
7. 上游是 `cloudcode-pa.googleapis.com`（非公開 Gemini API）
8. `remainingFraction` 打 20 發前後 7 位小數不變 → 非即時計量器
9. keeper SQLite 可讀、含 `reasoning_tokens` 與 per-key 歸帳；`usage-queue` 已被 keeper 抽乾；keeper HTTP API 有 CSRF 防護不可 curl
10. relay 無跨模型 fallback（僅憑證層 + fallback base URL）
11. relay `quota-exceeded.antigravity-credits: true`（會自動用 credits）
12. relay 版本 7.2.75；`/v0/management/antigravity-credits` 回 404（credits 餘額不可讀）

**B. 官方文件確立（有 URL + 逐字引文 + 抓取日 2026-08-17）**
1. **Gemini CLI 於 2026-06-18 停止服務 Google AI Pro / Ultra**；官方取代路徑 = Antigravity CLI（developers.googleblog.com 公告，文章日 2026-05-19）
2. Cloud 額度頁已下架消費者列，只剩 Code Assist Standard 1500 / Enterprise 2000（`Last updated 2026-08-11 UTC`）
3. Antigravity AI Pro = "refreshed every five hours until weekly limit reached"，**無公布數字**、按 agent 工作量計、"subject to modification"
4. Antigravity 額度耗盡 → 用 purchased AI credits（設定 Never / Always），**不是換模型**
5. Antigravity Gemini 池內 **flash 與 pro 共用額度、無分池**；面板只分 Gemini 與 Claude/GPT 兩池
6. Gemini API rate-limits 頁**已不公布每模型 RPM/RPD/TPM**（5 張表全數點名、無一為速率表），改導向 AI Studio 登入頁
7. Gemini API tier 階梯（Free / Tier 1 / 2 / 3）**只認 Cloud Billing 付款紀錄**；三頁對 `"AI Pro"` / `"Google One"` 出現次數全 0
8. Gemini CLI 的 fallback **預設是詢問使用者**、唯一靜默鏈方向是 `flash-lite → flash → pro`（不是 pro → flash）
9. Haiku 4.5 = `claude-haiku-4-5`、$1.00/$5.00 per MTok、200K context
10. **⛔ Antigravity ToS §6 逐字禁止「用第三方工具/服務存取本服務」，舉的例子是 "using OpenClaw with Antigravity OAuth"，明文 "is a breach of this Agreement" + "may be grounds for suspension or termination of your account"**
11. Antigravity FAQ 點名 Claude Code / OpenClaw / OpenCode，並建議改用 "a Vertex or AI Studio API key"
12. Google APIs ToS §2c 三句：只能用文件描述的方式存取、憑證只能配對應 API、**不得偽裝 API Client 身分**
13. CLIProxyAPI 與 Google gemini-cli 打**同一個 host + 同一個 `v1internal` 版本面**（兩邊原始碼常數）；relay 主動偽造 UA 並每 6 小時同步 Google 的版本 manifest
14. **Google 官方（DevRel `jackwotherspoon`, `company: @google`）承認 2026-02～05 執法過 Antigravity 封號，起因逐字為 "the use of 3rd party tools or proxies"；封鎖連帶 Gemini CLI + Code Assist；二犯永久封禁**
15. 實際回應為 `403 PERMISSION_DENIED / reason: TOS_VIOLATION / domain: cloudcode-pa.googleapis.com`（**封鎖點正是這條腿打的 host**）
16. 觀察到的影響範圍為**服務層**（4 筆一手報告確認 Gmail / web Gemini 仍正常）；整帳號終止 **0 案例**，但 Google 對此**拒答**
17. 執法自 2026-06 起靜默（`created:>2026-06-01` 搜尋 relay 歸因封號 `total_count 0`）
18. 排程／automation **本身完全合法**（三份條款全文讀過、無禁止條文；Google 官方 SDK 還內建 `every(60, ...)`）
19. "not for consumer use" 條款於 **2026-12-18** 才加入（archive 逐版驗證 0→0→0→0→1）
20. 免費層 prompt+output **會進訓練且有人工審閱**；付費層明文不會
21. 官方 CLI 額度表列免費 API key **250 RPD**（你要 240 = 96%）；同 repo README 另寫 1000 RPD，**兩者矛盾**
22. 付費 Gemini Flash-Lite 月成本 **$0.16–0.79**（實測 token 量），Batch API 再砍半
23. Cloud 額度頁 per-user「Requests per second: **2**」（10 路並發會超）

**C. 未驗證（不要當事實引用）**
1. **非 GitHub 管道的封號回報**（Reddit / V2EX / linux.do 未搜）——這是「執法已下降」判斷的**主要缺口**
2. **實際永久封禁的驗證案例**（政策明文存在，查不到案例）；**整帳號終止**（0 案例但 Google 拒答）
3. **Antigravity CLI 是否支援 headless / `-p`**——若支援，是「AI Pro 額度 + 合規」唯一交集，值得補查
4. `one.google.com/terms-of-service`、`BannedAccountDialog.tsx` / commit `ea48bd94`——由 subagent 取得，未親驗
5. relay 這條腿吃 AI Pro 級（5h 刷新）還是 Free 級（weekly 刷新）的 Antigravity 池——`currentTier: free-tier` 與 `resetTime` 間距**互相矛盾**
6. `GOOGLE_ONE_AI` credit 的餘額、單位（次數？美元？）、消耗率——四條查法全 404（見 2.4）
7. `remainingFraction` 的語意（per-account 消耗？fleet capacity？）
8. 公開 Gemini API key 的當前狀態（repo doc 43 天前快照，未重驗）；**免費層真實 RPD（250 vs 1000 矛盾）**
9. free tier / Tier 1-3 的具體 flash **RPM** 數值（**權威來源在 AI Studio 登入牆後，抓不到**）
10. **付費 Gemini API key 走 `generativelanguage.googleapis.com` 的實際延遲**——本輪所有延遲數字都來自 relay / `cloudcode-pa` 路徑。同級模型不該差一個數量級，但**改路後應重測 p50/p95**
11. Haiku 4.5 在此 prompt 下的實際 token 量與判讀正確率（**完全沒實測**，成本是 published pricing × 假設 token 量推算）
   — 這個缺口是**結構性擋住的、不是沒試**：【實測】`ANTHROPIC_API_KEY` 未設、`ant` CLI 未安裝、relay `/v1/models` 無 haiku（relay 上的 `claude-sonnet-4-6` / `claude-opus-4-6-thinking` 走 Antigravity 免費池，**不能**當 Haiku 付費成本的代理量測）。要補這格需要一把 Anthropic API key。
12. 「每模型 RPM 表曾存在後被移除」的時序（web.archive.org 本輪 502/503 全掛）
13. Cloud 額度頁的「2 RPS」是否套用在 Code Assist / Gemini API 個人層（該頁是 Cloud / Workspace 視角，標為部分適用）

**D. 官方文件自身的矛盾（引用時要小心）**
1. `google-gemini/gemini-cli` repo 的 `README.md` 寫 free tier「1000 requests/day」、同 repo `quota-and-pricing.md` 寫「250 requests」
2. geminicli.com 同一頁：頂部橫幅寫「Google One users: Gemini CLI will be replaced on June 18th」、第 223 行照舊列 `| Google AI Pro | 1,500 requests |`
3. one.google.com 把 AI Studio 列為 AI Pro 權益；gemini.google/us/subscriptions 的 AI Pro 清單 `"AI Studio"` 出現 0 次
4. `quota-and-pricing.md` 的 anchor 連到 Cloud 額度頁稱「Individual 額度」，但目標頁已無 Individual / AI Pro / AI Ultra 任何一列（斷掉的引用）
5. Gemini 3.7 Flash 只出現在 Batch Tier 1 表，Tier 2/3 表沒有它

**E. 什麼會翻案**

主結論（「別走 relay、改付費 API key」）要被翻案，需要下列任一：

- **Antigravity ToS §6 出現我沒看到的例外條款**（全 8 條讀過、沒有），或 Google 公開撤回那條禁令。
- **CLIProxyAPI 的 Antigravity 路徑實際上不打 `cloudcode-pa`**（原始碼常數直接否定，可能性極低）。
- **官方 Antigravity CLI 確認支援 headless** → 出現「AI Pro 額度 + 合規 + $0」的第三條路，主推建議要改（見 5.3 附註）。

次要結論的翻案條件：

- 若出現任何一筆有 Google 通知信原文、顯示 **Gmail / Drive 一併失效**的第一手案例 → 3.4 的「影響範圍限於服務層」判定要上調，risk 從「服務中斷」升為「帳號級」。
- 若 2026-06 之後出現**新的 relay 歸因封號** → 3.4(d) 的「執法已靜默」要撤回。（我只搜了 GitHub，這是主要缺口）
- 若查到免費層真實 RPD **明顯高於 250** → 免費 key 重回備援階梯的候選（但「資料進訓練」那條理由仍在）。
- 若 `GOOGLE_ONE_AI` credit 餘額查得到且顯示會被消耗 → 2.4 的建議從「預防性」升為「已在流血」。
- 若 Haiku 4.5 實測 token 量顯著高於 83/34 → 5.1 / 5.3 成本表重算。
- 若 Google 把 AI Pro 重新列回 Gemini CLI 支援層級 → 2.2 的退役判定要撤回。

---

## 修訂紀錄（同一份研究內的自我修正，留著避免下游引用到錯的版本）

| 我原本寫的 | 修正為 | 觸發 |
|---|---|---|
| 「AI Pro = 1,500 req/day with Gemini CLI」 | **已過期**——Gemini CLI 2026-06-18 停服 AI Pro | 官方公告 |
| 「路徑 B 是灰色、務實可接受」 | **明文違約 + 有實際執法紀錄** | Antigravity ToS §6 + Google DevRel 公告 + 403 payload |
| 用「no support for BYOK」推論「Google 態度是不保證而非違規」 | **推論站不住**——同體系有明文 "is a breach of this Agreement" | 同上 |
| 峰值「207 RPM」 | **尖峰 10 RPM / ~10 RPS**（207 是把 2.9 秒外推成一分鐘的錯誤框架） | 重新計算 |
| 備援階 2 = 免費 Gemini API key | **排除**——250 RPD 只夠 96%，且資料進訓練 | 官方 CLI 額度表 + terms |
| 主推 = relay（Antigravity），備援 Haiku | **主推 = 付費 Gemini Flash-Lite（$0.16–0.79/月）** | 條款 + 成本重算 |
