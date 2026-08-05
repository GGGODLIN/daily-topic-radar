# social-info / daily-topic-radar

## 路徑

實體在 `~/code/social-info`；`~/Desktop/projects/social-info` 是 **symlink**（macOS TCC 擋 LaunchAgent 進 ~/Desktop、FDA 不繼承到 homebrew Python child binary，為了 launchd 跑 daily fetch 做的雙路徑）。`~/.claude/projects/` 也有對應 symlink，兩條 cwd 進來 hit 同一份 memory + session。

**硬編路徑用 physical path** (`/Users/linhancheng/code/social-info`)，不要用 Desktop 那條（launchd 啟 process 會 resolve 但 TCC 仍會擋）。

## 自動排程

- **Label**: `com.gggodlin.social-info-daily`
- **Plist**: `~/Library/LaunchAgents/com.gggodlin.social-info-daily.plist`
- **Schedule**: 每天 06:00 Asia/Taipei（launchd `StartCalendarInterval`）
- **Wrapper**: `scripts/run-daily.sh` — VPN pre-check + `uv run python -m social_info` + `git add state.db reports/` + `commit` + `push`
- **Log**: `logs/cron-{date}.log`（gitignored）

**VPN / 雲端出口 pre-check（2026-08-05 加）**：`run-daily.sh` 起跑前先跑 [`scripts/vpn-precheck.sh`](/scripts/vpn-precheck.sh) 查對外出口 ASN。**刻意「偵測不擋跑」**——擋下等於當天零資料、比殘缺更糟；命中只做三件事：cron log 印 `⚠️ VPN-PRECHECK 命中`、寫 `ALERT-vpn-precheck.md` 到 repo root（gitignored、出口恢復正常那天自動刪）、跳桌面通知。判準是**出口 ASN 不是「有沒有 utun 介面」**（Tailscale 這類 split-tunnel 走 utun 但不影響出口、用介面判會誤殺）。查不到 / 逾時 / 無網路一律放行（exit 20），這道 gate 絕不自己弄垮 daily run。改它或改 `run-daily.sh` 的 `VPN_PRECHECK_START/END` 段後**必跑** `bash scripts/vpn-precheck.test.sh`（25 項，含 set -e 下不中斷、ALERT 檔生成與清除）。

電腦睡眠時 launchd 會在喚醒時補跑一次；整夜關機那天就 miss、不追補。

### 常用指令

```bash
# 手動觸發
launchctl kickstart -p gui/$(id -u)/com.gggodlin.social-info-daily

# 看狀態
launchctl print gui/$(id -u)/com.gggodlin.social-info-daily | grep -E "state|active count|last exit"

# 暫停 / 重新載入
launchctl bootout gui/$(id -u)/com.gggodlin.social-info-daily
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.gggodlin.social-info-daily.plist

# debug 直接跑 wrapper
bash scripts/run-daily.sh
tail -f logs/cron-$(date +%Y-%m-%d).log
```

## 本機分析 routine（不 commit）

`reports/local-analysis/` 是 [`~/.claude/workflows/local-analysis.js`](file:///Users/linhancheng/.claude/workflows/local-analysis.js) workflow 的 proposal markdown 輸出，user review 完手動 apply。`.gitignore` 已排除（個人 path + 分析結果不 commit）。

- **Channel 清單 / 頻率 / 沿革以 workflow 檔內 `CHANNELS` 陣列 + 檔頂註解為 SSOT**（2026-07-25 時點 22 個 channel = LLM 對內整理 13 + shell scan 9、daily 14 / weekly-tue 8；數字有疑義時以 `CHANNELS` 陣列現查為準、不以本行為據），本檔不列表、不重複維護
- 觸發：「今日本機分析」等語句走 hook 注入 workflow；備援 slash command `/daily-local`
- **2026-07-30 三分架構（改任何一層之前先確認你動的是哪一層）**：
  | 層 | 誰做 | 職責 |
  |---|---|---|
  | 偵測 | channel agent（workflow fan-out）| 掃檔 / 比版本 / 找 marker，並自驗 finding |
  | 算術與狀態閘 | [`scripts/local-analysis/ledger-reconcile.py`](file:///Users/linhancheng/code/social-info/scripts/local-analysis/ledger-reconcile.py) | `next_due` / `escalate_at` / observing-kept 靜默 / count 同日不重複 / 家族計數（讀 `rule-family-health.py`）/ 可查物存在性證據；**`status` 轉換只走 `--decide`** |
  | 排檔與根因 | main session | 語意比對、三檔 H/M/L、根因判斷、拍板回寫 |
  - **workflow 不再產 digest**（合成 agent 已移除），回傳結構化 channel 清單 + `reconcile_cmd`
  - 排檔規則的**單一來源**是 [`~/.claude/hooks/daily-local-analysis-trigger.sh`](file:///Users/linhancheng/.claude/hooks/daily-local-analysis-trigger.sh) 的 `msg` heredoc（關鍵字觸發時自動注入、走 `/daily-local` 時該 command 叫 main 去讀）；**每條規則的「為什麼」在 workflow script 的「合成 agent 已移除」註解段**，改規則前先讀
  - 動機：誤報常態性稽核（ledger 178 筆 / 49 天）顯示 21 筆（12%）是 channel 判斷本身錯，且集中在 6 個可修根因；合成 agent 只有 3-5 行濃縮、沒有原始素材與查證工具，只能靠推論補因果
  - 回歸測試：`bash scripts/local-analysis/ledger-reconcile.test.sh`（35 項）＋ `bash ~/.claude/hooks/daily-local-analysis-trigger.test.sh`（27 項）
- 原 launchd 排程 2026-05-26 已停用（plist 在 `~/Library/LaunchAgents/disabled-local-analysis-2026-05-26/`，要恢復 mv 回去 launchctl load）；沿革見 memory `reference_cc_workflow_tool_2026_05`
- bumblebee channel = 純 Go binary 供應鏈掃描（不耗 LLM）：findings > 0 寫 `ALERT-bumblebee.md` 到 repo root + osascript 通知；設計見 [`scripts/local-analysis/bumblebee-daily.sh`](file:///Users/linhancheng/code/social-info/scripts/local-analysis/bumblebee-daily.sh) 檔頭
- 設計原則：input 不改、output separate、嚴格 read-only（例外 = 各 channel prompt 明文要求的 ledger 寫回）；細部設計背景見 memory `reference_cc_recap_design_2026_05_12`（recap）/ `reference_claude_config_dir_multi_account`（symlink drift）

## TCC / FDA setup（一次性）

`/bin/bash` 已加 Full Disk Access（System Settings → Privacy & Security → Full Disk Access）。

**注意**：未來 `brew upgrade python` 換版本後，新 Python binary（`/opt/homebrew/opt/python@<version>/bin/python<version>`）也要加進 FDA，不然 launchd job 會死在 `python: realpath: .venv/bin/: Operation not permitted`。.venv 重建後 `.venv/bin/python` symlink 會指到新版本，舊 FDA 不夠。

## GitHub Actions

`.github/workflows/daily.yml` 只剩 `workflow_dispatch:`（手動 trigger backup），原本的 cron `schedule:` 已拿掉——cloud IP 跑 Reddit 被擋。Reddit 抓法細節見「已知 fetcher gap」段（該段是 SSOT）。

**操作注意**：06:00 launchd 觸發前 VPN 應該關著（消費級 VPN exit IP 連 old.reddit HTML 都可能整個被 ban；**雲端 ASN 出口更死**——2026-08-05 實測 WireGuard 走 AWS Tokyo AS16509 時 5 個 sub 全 403，關掉後同樣 5 個全 200），或設 split-tunnel 把 reddit.com 排除走真實出口。忘了關不會靜默失敗：「自動排程」段的 VPN pre-check 會在 06:00 當下就發桌面通知 + 寫 `ALERT-vpn-precheck.md`。

## 「今日分析」入口語意

這個專案有兩條 daily routine，是**分開的兩件事、各自獨立 session 處理**（2026-05-19 使用者明確指正，對應 memory `feedback_digest_excludes_local_analysis.md`）：

1. **今日話題分析 = Stage-2 daily digest**：`reports/digest-{date}.html`，手動 trigger 產出 ecosystem 個人化整理。詳「Stage-2 digest」。
2. **今日本機分析 = 本機分析 multi-channel workflow**：`reports/local-analysis/{date}-{channel}.md`，2026-05-26 起走 on-demand workflow（[`~/.claude/workflows/local-analysis.js`](file:///Users/linhancheng/.claude/workflows/local-analysis.js)），原 launchd 排程已停用。詳「本機分析 routine」。

- 使用者說「今日話題分析」/「daily 分析」/「跑日報」/「產 digest」→ **跑 stage-2 digest workflow**（[`~/.claude/workflows/daily-topic-analysis.js`](file:///Users/linhancheng/.claude/workflows/daily-topic-analysis.js)，2026-05-26 起）：pre-flight（KNOWN_ISSUES.md + WATCH.md + PROBES.md stage-2 fetch + external-feeds）→ 主軸抽取 → URL fact-check → 吐 JSON 給 main session 接寫 HTML。**不掃 `reports/local-analysis/`、digest「系統當天動態」段只寫 digest pipeline 自身（不寫本機 channel 跑了沒 / 失敗沒 / drift proposal）**。
- 使用者說「今日本機分析」/「跑本機分析」/「跑一下本機分析」/「每日本機分析」→ 走 hook 觸發 [`~/.claude/workflows/local-analysis.js`](file:///Users/linhancheng/.claude/workflows/local-analysis.js) workflow，按 weekday 篩 channel 後 fan-out + 合成 digest 給使用者。
- 兩條都是「daily 分析」家族、都住這個 repo，但 digest session 不代管本機那條。

**未來新增的 daily 分析 routine 預設都放這個 repo**（無論 ecosystem digest、setup audit、跨專案掃描、wiki 整理），不要散到 ako-marketing-admin / cc-i18n-proxy / personal-site 等其他專案。對應 memory `project_daily_analysis_scope.md`。

## Digest 後續 apply 的派工預設

使用者拍板 digest 項目（H1 做 / M2 做…）後執行時：

- **內容合成型**（wiki promote / wiki refresh / 長篇 report 改寫——需讀 3+ 檔或 10K+ tokens 素材再產出）→ **預設派 subagent**，模型由當下 session 按 subagent-routing 三分類自判；main 落檔後必驗收（sanitize / schema / 引用完整性），驗收標準寫進派工 prompt
- **診斷型**（計數 mismatch / drift / 「疑似壞了」）→ main 自己做，先找根因再改，不派（subagent 會照字面修、抓不到誤報與深層根因）
- **機械批次 ≥ 5 檔** → 派（模型自判）；< 5 檔 main 一發 loop
- **ledger / STATE 回寫、workflow prompt / rules 修改** → 一律 main
- 長跑指令照舊走 Bash run_in_background

## Stage-2 digest

`reports/{date}.md` 是 raw aggregator 輸出（06:00 launchd 自動產生）。`reports/digest-{date}.html` 是 Claude 個人化整理（**手動 trigger**，使用者叫我產才做）。

**執行主體是 [`~/.claude/workflows/daily-topic-analysis.js`](file:///Users/linhancheng/.claude/workflows/daily-topic-analysis.js) workflow（2026-05-26 起、HTML 撰寫 2026-07-04 起派 sonnet subagent）。Pre-flight 攔截 protocol（KNOWN_ISSUES / WATCH / PROBES stage-2 fetch / external-feeds backstop）、主軸抽取、URL fact-check 分流、digest HTML 鐵律（🛠 GitHub 倉庫觀察獨立段 / 🟣 Anthropic·Claude 動態獨立段 / v3 範本 / resurface marker）——細節全部以 workflow script 內嵌 prompt 為 SSOT，改規則改那裡、不改本檔。**

Main session 需要知道的：

- **workflow 三道 fail-fast guard（2026-07-28 加）**：workflow 會在早期中止並回 `aborted: true` + `abort_stage` + `next_action`，main session 拿到 result 的第一步是判 `aborted`、為 true 就跳過 digest_write / digest_audit / verify gate 那套流程。三個 stage：`input_precheck`（raw md 缺檔或截斷 → 補跑 aggregator 再重跑）、`known_issues_gate`（retry 完仍有 🚨 → **停下來問使用者**「先處理還是接受缺口」，接受才用 args `{"date":"…","accept_gaps":true}` 重跑，main 不得自行決定）、`themes`（主軸抽空 → 查 raw md 與 journal 找因，不原樣重跑）。動機：2026-07-28 raw md 缺檔 + reddit 全 403，workflow 兩次跑到一半才被手動打斷、白燒約 149 個 agent。門檻常數 `MIN_RAW_MD_BYTES` / `MIN_RAW_MD_SECTIONS` 在 workflow script 內，調它等於關掉保護
- **🚨 條目的處理路徑**：retry 由 workflow 的 KnownIssuesGate 自動跑（≤ 2 次），retry 後仍失敗才走上面的 `known_issues_gate` abort；最常見成因是 VPN 開著讓 reddit 整域 403（關掉後 `uv run python -m social_info --retry-failures` 即可補回）。選擇接受缺口時 digest 開頭要明寫缺口；🛠 / 🪦 surface 不阻塞
- **三份攔截檔都在 repo root、手動 maintain**：`KNOWN_ISSUES.md`（pipeline 自動寫，四區 🚨/🛠/🪦/⏳）、`WATCH.md`（被動 grep raw md 的 upstream bug 清單）、`PROBES.md`（主動 fetch 外部 source 的訊號清單）——各檔自帶 schema + 維護規則，新增 entry 照檔內規範 append
- **手動觸發 probes**：使用者說「跑 probes」→ `bash scripts/local-analysis/probes-daily.sh`（overwrite 當天 probes report、對所有 entries 跑）
- **external-feeds backstop**：follow-builders feed 定位是「自家 source 漏抓 backstop」非主軸校準；feed 404 不阻塞 digest；上游 repo 消失 → 移除該 pre-flight step；試讀結案數據見 memory `reference_follow_builders_trial_2026_06_05`

### URL 抓取路由

digest 階段展開原文（解讀 / 摘要 / 引用）時**按來源分流**。一般研究 / 對話用 WebFetch（見 [`~/.claude/skills/research-before-answer/SKILL.md`](file:///Users/linhancheng/.claude/skills/research-before-answer/SKILL.md)）。

**不觸發**：搜尋結果列表（用 WebSearch）／ GitHub 元資料（用 `gh`）／ SDK 文件（用 Context7）。

#### 路由

| 來源 | 主要工具 | 備援 | 備註 |
|---|---|---|---|
| `reddit.com` 全域 | `~/.claude/scripts/fetch-fallback.sh <url>`（reddit_track 第 1 階走 arctic-shift API）| `mcp__chrome-devtools` MCP（selftext 缺失時走、不是 claude-in-chrome）| WebFetch + claude-in-chrome navigate 兩條對 reddit 整域擋；現主力 arctic-shift（[[reference_arctic_shift_reddit_api]]），selftext 缺才升 chrome-devtools（[[reference_chrome_devtools_mcp_reddit_escape]]）|
| X / Twitter | `~/.claude/scripts/x-fetch.sh <url> --full`（headless、免帳號；syndication CDN + FxTwitter v2 conversation/quotes）| `claude-in-chrome` MCP（sensitive interstitial / 需登入才看得到的內容） | 2026-07-11 從 chrome 主力搬到 headless；`fetch-fallback.sh` 已自動分派 x/twitter domain → x-fetch.sh；見 `reference_x_tweet_fetch_fallback.md` |
| 需 cookie / SPA / JS render / 大站動態 | `claude-in-chrome` MCP（帶 daily Chrome cookie）| `chrome-devtools` MCP headless | fact-check 主力；見 `reference_chrome_devtools_mcp_default_behavior.md` + `reference_chrome_fallback_extraction_pattern.md` |
| Cloudflare 系列（ithome.com.tw / thehackernews.com 等）| `~/.claude/scripts/fetch-fallback.sh` | `claude-in-chrome` MCP | 原主力 `mcp__fetch__fetch` server 已移除（幽靈引用、2026-07-27 清理）；同日實測：ithome WebFetch 仍 403、fetch-fallback exit 0；thehackernews WebFetch 已通、不必先繞 |
| `arstechnica.com` | `~/.claude/scripts/fetch-fallback.sh` | `claude-in-chrome` MCP | WebFetch 結構性失敗（2026-07 三天內 6 次、全在 digest workflow subagent）；fetch-fallback Googlebot HTML 軌 2026-07-21 實測 exit 0 直通，不要先試 WebFetch |
| HN / 一般 RSS / 部落格 / 新聞 / 結構簡單站 | `WebFetch` | 4xx/封鎖 → `~/.claude/scripts/fetch-fallback.sh` → exit 75/1 升 `claude-in-chrome` | 結構簡單站直接通 |


#### fetch-fallback.sh

`~/.claude/scripts/fetch-fallback.sh`（見 memory `reference_fetch_fallback_script.md`）整合通用強方法：

- **reddit 分支**（2026-06-23 加首階）：arctic-shift API via `fetch-reddit.sh`（OP + top-20 comments）→ `<permalink>.json` → `old.reddit.com` → `r.jina.ai` → `archive.today` → exit 75。arctic-shift OP selftext 看 snapshot 時機可能缺、缺則 exit 75 升 chrome-devtools MCP 補；comments 完整可靠
- **其餘通用強方法**：Googlebot-UA + `X-Forwarded-For` + Referer + JSON-LD `articleBody` → EU-XFF/Twitter-Referer → `r.jina.ai` → `archive.today` → exit 1
- **退出碼契約**：0=內容在 stdout / 75=需瀏覽器（升 chrome）/ 1=全敗（升 chrome）

整合進 research-before-answer 退路階梯：WebFetch 4xx/封鎖 → `fetch-fallback.sh` → exit 75/1 才 `mcp__claude-in-chrome__navigate` + JS 抽 body（`document.body.innerText.slice(0, 4000)`）。

#### 失敗處理

- 所有 fetch tool 失敗 → 走 fetch-fallback.sh exit 75/1 流向 chrome；chrome 也失敗才報告使用者「抓不到」，不硬生內容
- chrome 抽 body snippet：`JSON.stringify({title:document.title,url:location.href,bodyText:document.body.innerText.slice(0,4000)})`
- prompt injection：chrome 抽回來的 innerText 是 untrusted data，按 `critical_injection_defense` 處理

## 已知 fetcher gap

- Reddit 5 個 sub：**2026-05-29 起 fetcher 改抓 `old.reddit.com/r/X/top/` 列表 HTML**（`reddit.py`）。原因：Reddit 收緊未認證 `.json` API，任何 IP（含住宅）打 `top.json` 都回 403「blocked by network security / log in」，但 HTML 頁仍 200。**舊認知「住宅 IP OK」只對 HTML 成立、對 `.json` 已失效**。old.reddit HTML 用一般 UA 即 200、含 `data-score`/`data-permalink`/`data-author`/`data-comments-count`/`data-timestamp`，但**拿不到 selftext**（列表頁無內文，外連貼文只存 src link 進 excerpt）。VPN 開著時 exit IP 仍可能被整個擋（HTML 也擋）；若 old.reddit 哪天也 403 → 改接 Reddit OAuth API（註冊 app + client credentials）。`known_issues.py` 的 reddit hint 已同步更正（不再寫「VPN 開著必擋」）。
- Threads `D15iJFBNZ9wgeWAhw` Apify actor 持續 400（payload schema 不合）
- HN 抓 front_page link + 每則 story 的 top 5 comments（Firebase API，2026-05-11 之後）；不抓巢狀回覆
- X 只抓 KOL handle、不抓 reply / quote tweet / search query
- **⚠️ pipeline hang bug（2026-06-05 事故、未根治）**：06:00 launchd run `python -m social_info` **卡死 6 小時零進展**（CPU 累積僅 1.24s）、raw md 沒產出、`state.db` 停在前一天。手動 kill + 08:59 重跑：10 秒產出 107KB raw md，但**重跑也卡在「寫完 raw md 後退出」階段**（同源 hang）、再 kill 一次。兩次卡點不同（fetch 階段 vs exit 階段）但同病根。**timeout 不是缺失**（`pipeline.py` 有 `HTTP_TIMEOUT=httpx.Timeout(30.0, connect=10.0)` 全局 + 各 fetcher override），推測根因是**某 fetcher 內有 sync blocking call（HTML/lxml parse 等）block 整個 asyncio event loop → httpx timeout timer 不觸發**，或 exit 階段 httpx client / 背景 task 沒乾淨收尾。**止血**：`scripts/run-daily.sh` 已加 hard-timeout 看門狗（600s 輪詢、超時 `pkill -f social_info` 砍 process tree + log，不阻 commit 已產出資料）。**根治待辦**：reproduce + 對每個 fetcher 加 logging 找出卡哪個 source，或把 sync parse 丟 `asyncio.to_thread` / 給 `asyncio.gather` 包 `asyncio.wait_for` 整體上限 + 確保 client 收尾。

對應 memory `feedback_digest_signal_coverage.md`：digest 缺社群討論層、要 surface 為 fetcher gap 不要默默 ignore。
