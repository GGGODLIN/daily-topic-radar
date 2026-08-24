# 01 — 建立 `ccp-free-watch` daily wrapper

**What to build:** 每日本機分析可執行一個 read-only `ccp-free-watch` channel，安全讀取目前 `ccp-free` route 與 OpenRouter credential 狀態，偵測 active model 退場／改價／失效及合格的新 preview；沒有決策時完全 silent，有決策或監控錯誤時產生可查證 report。

**Blocked by:** None — can start immediately.

**Status:** completed

**Needs:** 本機已有 `ccp-free` install metadata 與 mode-600 runtime secret；測試使用 temp fixture 與 fake HTTP，不需要真 credential。最終 confirmation 才需要目前有效的 OpenRouter key。

**TDD:** required

**TDD seam:** 呼叫者觀察到的 wrapper CLI 行為：exit status、report、baseline、stdout／stderr，以及 fake HTTP 收到的固定 synthetic request

- [x] 先寫會失敗的 focused wrapper test，使用 temp runtime、temp baseline 與本機 fake Models／Endpoints／Key／inference endpoints。
- [x] 健康 active route 且狀態未變時建立或更新非敏感 baseline，canonical report 內容嚴格等於 `__SILENT__`。
- [x] Active exact model 消失、prompt／completion 任一價格非零、endpoint 為空、credential 已過期或權限不符時產生 finding report，不修改 route。
- [x] Key API 只輸出 `expires_at`、limit 與 free-tier 等非敏感欄位；provider key 只存在 process memory，不進 command line、stdout、stderr、report、baseline 或測試輸出。
- [x] Candidate discovery 只接受零 token 價格、coding／agent 用途、必要 tool parameters、足夠 context、無近期到期且沒有額外 per-request／media 收費的 preview／alpha／stealth model。
- [x] Metadata 合格候選只收到固定無敏感內容的 prompt 與 forced tool-call synthetic Gate；成功才產生「可考慮開 trial」finding，失敗只留診斷、不宣稱候選可切換。
- [x] 單次 429、5xx 或 timeout 不誤判 active model 已退場；API／parser／監控失效不得冒充 silent，必須產生錯誤 report。
- [x] Baseline 只保存 exact model、零價狀態、endpoint availability、candidate IDs、key expiry 日期、terms hash 等非敏感狀態。
- [x] Finding 只提供現象、證據與「處理／忽略／延後」所需資訊；不得自動切換、不得寫 pending-actions ledger、不得使用 `openrouter/free`。
- [x] Wrapper 所有可報告狀態使用既有 shell-channel 成功退出語意；真正的 script／report contract 失敗才非零退出。
- [x] Focused wrapper test 轉綠，並用 fake secret 做 exact scan，確認所有產物與輸出非法命中為 0。

## Verification Log

- RED：wrapper 不存在時 focused CLI suite 為 0 PASS／3 FAIL；active model、價格、endpoint、expiry、candidate Gate 與 provider policy 逐 slice 先出現對應 failure。
- GREEN：focused wrapper suite 最終 110 PASS／0 FAIL；涵蓋 healthy silent、hard-state transition dedupe、candidate prompt＋forced tool Gate、failed Gate 隔日重試、active／candidate metadata 與全價格欄位、API schema、redirect、terms drift、mode 600、report transaction 與逐輪 fake secret 零外洩。
- 真實 zero-price guard：`provider.allow_fallbacks=false`、`require_parameters=true`、`max_price.prompt/completion="0"` 對 exact `stealth/ox-alpha` 回 HTTP 200、exact model、固定 marker match。
- 真實 read-only probe：產生 key 將於 7 天內到期 finding；exit 0、baseline 同時保留 terms 與 candidate 欄位，report／baseline／log／stdout／stderr real secret 命中 0。
- Review reception：修正 exception／provider-controlled 字串 redaction、HTTP redirect bearer 保護、API envelope validation、active metadata／endpoint／全價格 Gate、exact single tool-call Gate、hard-state baseline merge、report-before-baseline transaction、同輪 candidate＋terms 合併、naive UTC、dotenv export、malformed candidate isolation與 Decimal canonicalization。低風險未改項只剩 report／log mode 644；內容已驗證不含 credential，父目錄是使用者本機 analysis output，接受既有 channel 慣例。
- Criterion correction：failed prompt Gate 只會發出第 1 個 request，因此隔日重試 assertion 應為 1 call，不是 2；已依實際 public behavior 修正測試，production 行為不變。
