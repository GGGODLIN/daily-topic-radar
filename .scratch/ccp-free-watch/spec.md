## Problem Statement

`ccp-free` 目前固定使用一個 OpenRouter model。OpenRouter 會不定期提供零價格的匿名 preview model，也可能在沒有長期承諾的情況下讓它退場、改價或失去 endpoint。使用者已經每天執行「每日本機分析」並透過既有 H／M／L digest 與 pending-actions ledger 拍板，因此新的偵測不應建立另一個 dashboard、通知通道或 trial 催辦迴路。

## Solution

在既有每日本機分析 workflow 新增一個 `ccp-free-watch` daily shell channel。它每天讀取本機 `ccp-free` 非敏感 metadata、檢查 credential 狀態，並查詢 OpenRouter model、endpoint 與 key API。沒有狀態轉移時回傳既有 silent sentinel；需要使用者決定時產生可被既有 digest 排檔的 finding。main session 繼續依通用規則把 finding 放進 H／M／L，並透過既有 ledger 保存未處理項目。

## User Stories

1. As a 使用者, I want 每日本機分析偵測 OpenRouter 新的測試期免費模型, so that 我能在既有拍板流程決定是否把 `ccp-free` 切換過去。 [user: "ccp-free在openrouter有測試期免費模型時希望可以通知我換上"]
2. As a 使用者, I want active preview 退場、改價或不可用時收到 finding, so that 我能決定切回穩定可用的 exact model。 [user: "退場時也要通知我換成穩定可用的模型"]
3. As a 使用者, I want 決策直接出現在每日本機分析的 H／M／L digest, so that 它會進入我實際閱讀與處理的地方。 [user: "其實決策直接放在每日本機就好，只是要放到我看得到，會處理的地方，不能只是出報告"]
4. As a 使用者, I want unresolved findings 沿用 pending-actions ledger, so that 沒回覆不會被當成已處理或隔天消失。 [evidence: daily-local-analysis-trigger.sh 的 reconcile 與拍板規則]
5. As a 使用者, I want 沒有狀態改變時 channel 完全 silent, so that 每日本機分析不會因例行健康資料增加噪音。 [evidence: 既有 daily shell channel 的 `__SILENT__` contract]
6. As a 使用者, I want 新 preview 先通過零價、tool、context、endpoint 與固定 synthetic Gate, so that 只有具備基本 Claude Code 候選資格的 model 才占用拍板頻寬。 [evidence: 本次 OpenRouter／Ox Alpha 研究結論]
7. As a 使用者, I want synthetic Gate 只送固定無敏感內容, so that daily scan 不會把 repo、session 或個人資料送給匿名 provider。 [evidence: Stealth EULA 與 privacy 研究結論]
8. As a 使用者, I want watcher 能讀取真實 key expiry, so that credential 到期不會被誤判成 model 故障。 [user: "a"]
9. As a 使用者, I want provider key 只存在 process memory, so that secret 不會出現在 report、baseline、log 或測試 fixture。 [evidence: ccp-free 現有 mode-600 secret 與 child-environment isolation]
10. As a 使用者, I want current route 的 exact model 來自本機 authoritative metadata, so that watcher 不會自行猜測目前在用哪個 model。 [evidence: ccp-free install metadata 與 launcher exact model]
11. As a 使用者, I want transient 429 或單次 API error 不直接成為切換 finding, so that provider 短暫抖動不會造成每日誤報。 [evidence: Ox direct Gate 曾出現一次 transient 429 後成功]
12. As a 使用者, I want watcher 查詢失敗時明確產生可見錯誤 finding, so that 監控失效不會被偽裝成「無變化」。 [evidence: 既有 shell channel error-report convention]
13. As a 使用者, I want model 消失、endpoint 為空或價格不再為零時優先呈現, so that `ccp-free` 不會默默改用付費或未知 model。 [evidence: ccp-free exact-model、no-fallback 決策]
14. As a 使用者, I want 新增功能只是一個 channel, so that 每日本機 workflow 的其他行為與既有 channels 不受影響。 [user: "請你注意這只是新增一個channel，可別把workflow本身大改版了，注意scope"]
15. As a 使用者, I want trial 到期催辦繼續走原本的 trial-review hook, so that 每日本機分析不會重新產生已明確排除的重複通知。 [evidence: feedback_trial_review_out_of_daily_channels]
16. As a 使用者, I want 拍板後仍由 main session 執行切換, so that watcher 本身保持 read-only，不會自動改 `ccp-free`。 [evidence: 使用者選擇既有「H1 做／忽略／延後」處理流程]

## Implementation Decisions

- 新增一個 daily shell channel wrapper，沿用既有 shell runner、report marker、silent sentinel、failed／channels 結構與 `needs_read` 行為。 [user: "看起來可以，那就這樣實作"]
- Workflow 只在既有 channel registry 新增一列；不得重構 workflow、改 hook prompt、改 digest 排檔、改 ledger reconcile 或修改其他 channel。 [user: "請你注意這只是新增一個channel，可別把workflow本身大改版了，注意scope"]
- Channel 無變化時只寫 `__SILENT__` 並成功退出；有 finding 時寫 Markdown report 並成功退出；wrapper 執行或資料來源異常時寫錯誤 report，不得冒充 silent。 [evidence: run-shell-channel 與既有 daily shell channel contract]
- Channel 不直接寫 pending-actions ledger；main session 依現有 generic digest 與 reconcile 規則處理 finding。 [evidence: daily-local-analysis-trigger.sh 的單一來源規則]
- Active route 的 model、endpoint 與 secret 檔路徑從本機 install metadata 取得；launcher exact model 可作一致性驗證。 [evidence: ccp-free install metadata 與 wrapper]
- Watcher 只能將 mode-600 runtime `.env` 中的 `OPENROUTER_API_KEY` 讀入 process memory。key value 不得出現在 stdout、stderr、report、baseline、command line 或 committed fixture。 [user: "a"]
- Watcher 使用 authenticated key API 讀取 `expires_at`、spending limit 與 free-tier 狀態；report 只能輸出非敏感欄位。 [evidence: OpenRouter current-key API schema]
- Watcher 使用 Models API 與 endpoint API檢查 active exact model 是否存在、prompt／completion 是否皆為零、endpoint 是否非空、必要參數是否存在、context 與明示到期日。 [evidence: OpenRouter Models／Endpoint API]
- Candidate discovery 只考慮零 token 價格、具 coding／agent 能力、支援 `tools`／`tool_choice`、context 達需求且沒有近期明示到期的 preview／alpha／stealth model。token price 為零但另有 per-request／media 費用的 model 不得誤列為免費候選。 [evidence: Lyria token-price-zero 但按歌曲收費的反例]
- 新候選通過 metadata Gate 後，watcher 可使用同一把 key直接送固定、無敏感內容的 prompt 與 forced tool-call synthetic Gate；不得送本機檔案、session 內容或 repo 資料。 [user: "a"]
- Candidate synthetic Gate 失敗只保留在 channel report 的診斷內容，不建立「建議切換」finding；active model 的 metadata hard failure則產生 finding。 [inferred]
- 單次 429、5xx 或 timeout 不視為 active model 退場；hard state transition 以 model 消失、價格非零、endpoint 為空、credential 過期或連續確認失敗為準。 [inferred]
- Baseline 只保存非敏感狀態，例如 exact model、價格、endpoint availability、candidate IDs、key expiry 日期與 terms hash；不得保存 provider key、proxy token、prompt 或 response content。 [inferred]
- Finding 只描述現象、證據與建議選項，不自行修改 route，也不宣稱未跑過 Harbor 的候選一定較強。 [evidence: daily-local 新 finding 查證與不得自行下根因規則]
- Active preview 退場但沒有 approved stable model 時，finding 應明寫「沒有已核准候補」，由使用者決定保持 unavailable 或另選候補；不得用 `openrouter/free` 自動填補。 [evidence: ccp-free exact-model、no-fallback 決策]
- Channel 屬本機基礎設施健康度，因此可進每日本機分析；trial review、產品 repo 待辦、dashboard、Discord、launchd 與自動切換均不在本次變更。 [evidence: local-analysis scope rules 與使用者本輪拍板]

## Testing Decisions

- 最高行為 seam 是 daily wrapper CLI。測試以 temp runtime、temp baseline 與本機 fake HTTP endpoint 執行，不接真 OpenRouter，不使用真 credential。
- Wrapper 行為測試須覆蓋：首次建立非敏感 baseline、有 finding 的 report、無變化 `__SILENT__`、active model 消失、價格非零、endpoint 為空、key 接近到期、符合資格的新 candidate、synthetic Gate 成功／失敗、API error report、secret redaction 與固定成功退出語意。
- Secret 測試使用明確 fake token，並斷言 stdout、stderr、report、baseline 與 test output 均不含 fake token。
- 第二個 seam 是既有 local-analysis routing contract。只新增 registration、daily due、source、outfile、runner prompt 與 `needs_read` assertions，不重測 runner 內部。
- 既有 generic runner、workflow report contract、routing、recurring promotion、hook 與 workflow syntax tests 作回歸證據。
- 實作完成後執行一次真實 wrapper probe；只能輸出非敏感狀態，且不得因此自動切換 model或寫 ledger。

## Out of Scope

- 重構 local-analysis workflow、shell runner、hook prompt、digest 或 ledger。
- 修改其他 local-analysis channels。
- 新增 dashboard、Discord、Slack、email、macOS notification 或 launchd 排程。
- 把 trial 到期／trial review 催辦放回每日本機分析。
- 自動切換 `ccp-free`、自動選擇 fallback 或使用 `openrouter/free`。
- 自動執行 Harbor benchmark。
- 建立 quota／usage ledger、cost-aware router 或通用 OpenRouter model 管理器。
- 修改 `ccp-free` route、FCC runtime、OpenRouter key、provider credential 或現有 launchd job。
- 讀取或傳送任何 repo、session、prompt history 或個人內容到 candidate model。

## Further Notes

`ccp-free-watch` 的責任只到「發現狀態轉移並產生可查證 finding」。是否開 trial、是否跑 Harbor、是否切換 model 與選哪個 stable候補，仍由使用者在既有每日本機 H／M／L 拍板迴路決定。

2026-08-24 implementation confirmation：使用者選擇允許 watcher 從 mode-600 runtime `.env` 將 OpenRouter key讀進 process memory。實作以 `provider.allow_fallbacks=false`、`require_parameters=true`、`max_price.prompt/completion="0"` 保護 candidate synthetic requests；真實 Ox probe 回 HTTP 200、exact model 與固定 marker。真實 daily probe產生「key 將於 7 天內到期」finding，未修改 route／trial／ledger，所有 probe 產物 real secret 命中 0。Failed candidate 不會被永久 suppress，下一次 daily run 會重新 Gate；只有 Gate-passed candidate 進 baseline seen set。

Review 修正後，active model 與 endpoint 共用全價格欄位、tools／tool_choice、context、到期與 status Gate；authenticated GET／POST 禁止 redirect；所有 API envelope 必須有合法 `data` schema；exception 與 provider-controlled model ID 在落 report／baseline 前 redaction／allowlist；synthetic response 必須恰好一個 exact marker tool call；baseline 在 report 成功後才提交，hard state 保留 terms／candidate 狀態，同輪 candidate、credential 與 terms findings 合併呈現。Canonical channel report與 baseline 已由既有 shell runner 真實產生，baseline mode 600、四項 canonical artifact secret scan為 0。
