# Probes — 主動拉外部訊號

> 手動 maintain 的「需要主動去查、不能等被動社群討論浮上來」的源。Agent 跑時讀此檔，對每個 active probe 跑對應工具 fetch、結果寫進 `reports/local-analysis/{date}-probes.md`（daily channel）或 surface 進 `reports/digest-{date}.html`（stage-2-digest channel）。
>
> 主動 = 直接拉外部 source（GitHub API / RSS / web）。被動比對既有 raw md 的東西放 `WATCH.md`。
>
> **觸發**（按 entry 的 `Triggered by` 欄位分流）：
> - **`daily`**：launchd `com.gggodlin.local-analysis-probes` 每日 06:45（在 4 channel 之後）→ 結果寫進 `reports/local-analysis/{date}-probes.md`
> - **`stage-2-digest`**：每次使用者叫 stage-2 digest 啟動時 agent 主動 fetch → surface 進 digest「🆕 ... 動態」section（不依賴 06:45 daily wrapper、避免低頻訊號 spam）
> - **`both`**：兩邊都跑
> - **手動**：使用者說「跑 probes」/「probes 一下」/「拉一下 probes」→ agent 讀 PROBES.md 對所有 entries 跑（不論 `Triggered by`）

## Active

### anthropics/claude-code release watcher

- **Why**: 使用者直接用 CC CLI（cmux + 多 worktree + ad-hoc session + launchd 周邊 routine）、上游每次 release 都可能影響 TUI 行為、hook 機制、daemon、MCP timeout、quota、deprecation。要在「每次叫 stage-2 digest 的當下」即時 fetch、從上次看的版本 diff 到最新、寫進 digest「🟣 Anthropic / Claude 動態」的「🆕 CC CLI release」小節（2026-06-20 起併區、原獨立段「🆕 CC CLI 動態」收為本區第一小節）。
- **Source(s)**: <https://github.com/anthropics/claude-code/releases>（via `gh release list anthropics/claude-code`）
- **How to fetch**: `gh release list --repo anthropics/claude-code --limit 10 --json tagName,publishedAt,name`，filter `publishedAt > Last seen`；對每個新版跑 `gh release view <tag> --repo anthropics/claude-code --json tagName,publishedAt,body` 拿 changelog 前 2000c。
- **Hit signal**: 最新 release `tagName` 比 `Last seen` 新 → 有新 release。
- **Action on hit**: stage-2 digest 寫進「🟣 Anthropic / Claude 動態」的「🆕 CC CLI release」小節、列每個新 release 的 tagName + publishedAt + body 重點 entry（按 hook / daemon / MCP / quota / deprecation / fast mode / agents 等分類）+ 每條標跟使用者 workflow 對應的個人化評論 + 顆星排序（★/★★/★★★）+ 升級指令。
- **Triggered by**: `stage-2-digest` only（**不在 06:45 daily probes 跑** — release 低頻、daily 跑會 spam；digest 階段才有差分價值）
- **Last seen**: `v2.1.201 (2026-07-03T23:50:35Z)` — 2026-07-04 probe baseline（★★★ 建議關注、subagent 失敗/rate-limit 回報修復直接打中 subagent-routing 失敗判斷根基 + `AskUserQuestion` 預設不再自動繼續 + 全 CLI permission mode 改名 Manual）。本次一次抓到 3 個新版本：**v2.1.201 (2026-07-03T23:50:35Z) ★** Claude Sonnet 5 session 不再用 mid-conversation system role 傳遞 harness reminder（內部訊息機制調整，行為面無直接感知差異）。**v2.1.200 (2026-07-03T16:52:33Z) ★★★** `AskUserQuestion` 對話框預設不再自動繼續（要 idle timeout 需去 `/config` 主動開，★★★ 直接影響「Skill 擇一」等依賴 AskUserQuestion 自動繼續的既有假設）；CLI/`--help`/VS Code/JetBrains 預設 permission mode 全面改名「Manual」（`--permission-mode manual` 與 `default` 並行接受）；修 background session 在 sleep/wake 後或重開 stalled session 時靜默中途停止；修 background agent daemon 因 crash 留下 stale `daemon.lock`（PID 被 OS 回收沿用）導致永遠無法再啟動；修 project-scoped plugin 在 git worktree 下無法正確載入（★★，對應 `superpowers:using-git-worktrees` 工作模式）；修 `claude agents --plugin-dir` 放在 `agents` 之後不顯示 plugin 的 agent/skill；改善 install script 記憶體不足時的錯誤訊息。**v2.1.199 (2026-07-02T23:35:18Z) ★★★** subagent 因 rate limit / server error 被截斷時，現在正確回傳 partial work 給 parent、並把 API error（如 usage limit reached）如實回報，不再誤報成功或靜默失敗（★★★，直接打中 subagent-routing.md「失敗後升降級路徑」判斷根基——過去 main 可能誤信 subagent 回報成功）；stacked slash-skill 呼叫（`/skill-a /skill-b do XYZ`）現在會載入全部前導 skill（up to 5）而非只吃第一個（對應「Skill 多 match 擇一」場景）；`SessionStart`/`Setup`/`SubagentStart` hook exit code 2 時 stderr 不再被吞、顯示在 transcript（直接影響 T2-routing-gate 等 hook debug 可見度）；修 background agent 在 macOS 透過 SSH cold-start 失敗（2.1.196 regression）；修 background agent daemon 在 Linux 因損毀 worker record 每 ~50 秒自砍全部 agent；修 `claude stop` 與 background-agent respawn race 時被悄悄復原；修 SSL/TLS proxy 憑證錯誤耗盡 retry 才顯示提示；修 idle subagent 從 agent panel 消失等多項。前一 baseline `v2.1.198 (2026-07-01T20:45:36Z)` — 2026-07-02 digest baseline（★★★ 建議升、Claude in Chrome GA + built-in Explore agent 改繼承主 session model 上限 opus 不再固定 haiku + background agent 完成後自動 commit/push/開 draft PR）：**v2.1.198 (2026-07-01T20:45:36Z) ★★★** Claude in Chrome 正式 GA（★★）；background agent 需輸入/完成時發 `Notification` hook `agent_needs_input`/`agent_completed`（★★）；新增 `/dataviz` skill（圖表/dashboard 設計 + 可執行色盤驗證器 ★）；Gateway 新增 Claude Platform on AWS (anthropicAws) 進 failover chain（★）；background agent 在 worktree 完成 code work 後自動 commit+push+開 draft PR、不再停下詢問（★★★，直接改變 launchd background workflow 收尾預期）；**built-in Explore agent 改繼承主 session model（上限 opus），不再固定跑 haiku**（★★★，直接打中 `~/.claude/rules/common/dispatch-and-verify.md` 的 Explore deny 前提——「固定 Haiku + 跳過 CLAUDE.md/memory/history」論述需重驗，deny 決策可能過時）；subagent + context compaction 繼承 session 的 extended thinking config（★★）；修短暫斷線（ECONNRESET）誤判整輪失敗、改重試（★）；修 sandboxed process 重複打同 host 觸發過量背景分類請求（★）；修 web/desktop/VS Code task panel 背景任務完成後卡 Running（★）；修 agent teams teammate 死於 API error 未上報 failed + 訊息喚醒卡住 teammate（★）；修 /diff 面板切分支/外部 commit 不刷新（★）；修 markdown 表格 fullscreen 溢出邊框（★）；修 Claude Platform on AWS/Mantle STS token 過期卡 /login（awsAuthRefresh 自動跑 ★）；修 macOS background agent local-network host no-route-to-host（宣告 Local Network entitlements ★）；修 /desktop 進出 worktree 後 Cannot determine working directory（★）；修 background agents view 開著時每 ~52 秒重複顯示 Reconnecting（★）；修 claude attach 內按 ← 誤退出到 shell（★）；修 claude --bg 搭配 --print/-p 靜默建立不可 attach session、現在直接拒絕衝突 flag（★）；前一 baseline `v2.1.197 (2026-06-30T17:56:37Z)` — 2026-07-01 digest baseline（★★★ 建議升、Claude Sonnet 5 成為 CC 新預設模型 + 原生 1M token context window + 促銷定價 $2/$10 每 Mtok 至 2026-08-31）：**v2.1.197 (2026-06-30T17:56:37Z) ★★★** Introducing Claude Sonnet 5：現在是 Claude Code 預設模型，原生支援 1M token context window，促銷定價 $2/$10 每 Mtok（至 2026-08-31 止），`claude update` 到 2.1.197 即可取得存取權；前一 baseline （★★★ 建議升、background session 存活 + Remote Control vendor 管控 + /deep-research 驗證結果修復 + streaming watchdog 預設開啟）：**v2.1.196 (2026-06-29T23:27:32Z) ★★★** 長時間 background session / workflow 在 process stop/restart/update 後存活；background agent worker 被 daemon restart 砍掉後下次打開 agents view 自動 resume；security: `claude mcp list/get` 不再 spawn repo 透過 committed `settings.json` 自行 approve 的 `.mcp.json` server；Remote Control 在 `ANTHROPIC_BASE_URL` 指向非 Anthropic host 時自動停用；fixed `/deep-research` 誤把 verifier 失敗報成「all claims refuted」而非 unverified；streaming idle watchdog 預設全面開啟；background job wake 永久刪 conversation bug 修復；org default models；`/code-review` 5 個 cleanup finder 合成 1 個；fixed Esc Esc regression；MCP OAuth invalid_scope 修復；session readable default name；fixed PowerShell git diff/grep 誤報；前一 baseline `v2.1.195 (2026-06-26T21:29:42Z)` — 2026-06-27 digest baseline（★★★ 建議升、hook hyphenated matcher 精確比對修復 + 背景 agent 資料遺失修復 + plugin consent 安全修復）：**v2.1.195 (2026-06-26T21:29:42Z) ★★★** hook matcher hyphenated identifier 精確比對修復（直接打中 T2-routing-gate）；外部 plugin 每次 loader 路徑要求 install consent；背景 agent 資料遺失修復；背景 agent daemon 控制 socket 失敗不再阻塞重啟；`CLAUDE_CODE_DISABLE_MOUSE_CLICKS`；voice dictation 修復兩則；Remote session provisioning checklist。

### ruanyf/weekly 新一期 watcher

- **Why**: 阮一峰《科技愛好者周刊》每週五出一期，內容近兩年高度集中在 AI 編程 / 模型經濟 / 軟體開發方法 / 工程師職涯，跟使用者 daily-topic-radar、interview-tour-2026、本機 AI 工具研究的選題重疊度極高（第 398 期頭條就是 OpenClaw 創辦人曬 Token 用量、Uber 燒爆 AI 預算、微軟棄用 Claude Code）。當成固定外部視角源，有新一期就在當天 digest 摘要+個人化評論。
- **Source(s)**: <https://github.com/ruanyf/weekly>（README.md 是全期索引、`docs/issue-N.md` 是各期正文）
- **How to fetch**: 用 authenticated `gh`（匿名 `curl` 打 GitHub API 會撞 rate limit 403）。取最新期號：`gh api repos/ruanyf/weekly/contents/README.md --jq '.content' | base64 -d | grep -oE 'docs/issue-[0-9]+\.md' | head -1 | grep -oE '[0-9]+'`。若 > Last seen，抓正文：`gh api repos/ruanyf/weekly/contents/docs/issue-<N>.md --jq '.content' | base64 -d`。
- **Hit signal**: 最新期號 > `Last seen` 的期號 → 有新一期。
- **Action on hit**: stage-2 digest 開「📰 科技愛好者週刊新一期」section、摘該期 12 個固定欄目（封面圖 / 本週話題社論 / 科技動態 / 文章 / 工具 / AI 相關 / 資源 / 圖片 / 文摘 / 言論 / 往年回顧）重點，標跟使用者 stack / 求職 / AI 工具研究對應的個人化評論。**正文裡的 GitHub repo 按 digest「🛠 GitHub 倉庫觀察」鐵律集中、不散落本段**。
- **Triggered by**: `stage-2-digest` only（**不在 06:45 daily probes 跑** — 週刊一週才一期、比 CC release 更低頻，daily 比對無差分價值徒增 gh call；digest 階段比對 Last seen 即可 catch）
- **Last seen**: `402（2026-07-03 发布《我在智念 AI 的日子（小说）》）` — 2026-07-04 probe baseline，下次 trigger = 第 403 期。内容=头条是一篇讽刺 AI 编程职场的短篇小说《我在智念 AI 的日子》（原文英文 getting-fried-part-1-cogentiv，中国化改写）——Token 消耗排行榜文化、员工不读 AI 生成代码直接合并 PR、用《西游记》地名给六个并行编程 agent 命名、AI 心理疏导机器人应对崩溃员工，与使用者「AI coding era engineer moat」研究主题直接呼应；科技动态=浣熊出现早期驯化迹象研究 / 圣地亚哥沙滩远程工作抗议返岗聚会 / AI 生成图片假花种子诈骗 / 美光与16个大客户签五年不跌价供货协议（内存因 AI 需求持续涨价）/ 特斯拉限制员工每周 AI 支出 200 美元（xAI 除外）；文章=设计模式被过誉只适合 Java 类僵化语言 / 如何设计良好 API / 中国未能突破喷气式发动机制造的原因；工具栏=Deno Desktop（跨平台桌面打包新选项，对标 Electron/Tauri）/ playCaptcha / Beer CSS / Image Toolbox（安卓开源图像编辑）/ EdgeMirror（开发者软件源边缘镜像网关，支持 PyPI/HuggingFace/GitHub/Docker/Go）/ Douzy（抖音批量下载）/ 涟漪鱼缸 WebGL demo。本期提及 GitHub repo 数条（Deno、playCaptcha、Image Toolbox、EdgeMirror、Douzy 等）——按 digest「GitHub 倉庫觀察」鐵律應集中列出、不散落本段。前一 baseline `401（2026-06-20 前后发布《如何赚到10亿美元》）` — 2026-06-26 digest 处理为 baseline，下次 trigger = 第 402 期。内容=Paul Graham 牛津演讲〈如何赚到10亿美元〉（月增长率 × 持续时间决定一切、进大市场、对用户同理心）/ Speedtest 被 Accenture 12 亿美元收购（数据生意本质）/ SQLite 作者 Richard Hipp「PR = 免费小狗」OSS 维护负担论述 / HTTP QUERY 方法正式引入 / Cloudflare+三大浏览器推 PACT 匿名令牌协议替代 Captcha；工具栏 LockIME（macOS 输入法锁定）/ PowerLens（Oh-My-Zsh 电源/风扇命令列显示）/ AnyDrag（macOS 不按标题栏拖视窗）/ MyKVM（跨平台 KVM 共享键鼠）/ SlopGuard（GitHub PR 品质评分）。前一 baseline `400（2026-06-12 发布《rsync 的争论》）` — 2026-06-12 digest 处理。内容=rsync 3.4.3 由 Claude 生成引发 OSS 社群争论 / AI 效率提升可否折成放假讨论 / Apple WWDC Siri 演讲场频率遮罩防唤醒细节 / Meta AI 客服被利用接管他人 Instagram 帐号漏洞；工具栏 ffmpeg webCLI / oproxy / smctl（Mac 风扇曲线+电池充电限制 CLI）/ ALTCHA / oak-keyring / math（MathML）。前一 baseline `399（2026-06-05 发布《中国 AI 大厂访问记》）`。

### Entry 範本

```
### <title — 簡短 probe 名>

- **Why**: <為什麼要 probe — context，之後 agent 整理結果時引用>
- **Source(s)**: <去哪查 — URL / repo / API endpoint>
- **How to fetch**: <agent 用什麼工具：`gh release view <owner>/<repo>` / WebFetch / `curl` JSON / Context7 / claude-in-chrome MCP>
- **Hit signal**: <什麼算「有新東西」— version > X / 上次 fetch 時間 / 特定 keyword 出現>
- **Action on hit**: <找到後做什麼 — inject 進當天 digest「外部訊號」段、標 priority、想抓細節 follow up>
- **Triggered by**: <`daily` / `stage-2-digest` / `both`> — `daily` 走 06:45 launchd probes wrapper；`stage-2-digest` 只在 digest pre-flight 由 agent 主動 fetch（適合 release 類低頻 / 高 cost / 需 user attention 的訊號、避免 daily spam）；`both` 兩邊都跑
- **Last seen**（optional）: <上次 baseline 值 — agent 每次 fetch 完更新這欄；空白代表第一次跑>
```

## Resolved / Archived

_(probe 不再 relevant 時移到這、保留歷史、一年後刪)_

## 維護規則

- **Hit signal 必填**：沒寫判準 agent 不知道 baseline 在哪、會每天 report 同樣內容；最簡單寫「上次 fetch 時間 / version / hash」
- **How to fetch 寫具體命令或工具**：不要寫泛泛「查 GitHub」，寫 `gh release view <owner>/<repo> --json tagName,publishedAt`，agent 才能照跑
- **Triggered by 必填**：`daily` / `stage-2-digest` / `both` 三選一。`daily` 走 06:45 launchd 自動跑；`stage-2-digest` 只在使用者叫 stage-2 digest 時 agent 主動 fetch；`both` 兩邊都跑。**daily 跑時 agent 過濾掉 `stage-2-digest` only 的 entries**（讀 PROBES.md 時跳過、不 fetch 不 surface）；**digest 啟動時 agent 讀 PROBES.md 過濾 `stage-2-digest` + `both` entries 跑**（不論 daily 是否已跑過、digest 啟動時都跑一次拿即時 diff）。
- **Last seen 由 agent 更新**：跑完 probe 後若 hit signal 達標，agent 把新 baseline 寫回對應 entry 的 `Last seen` 欄（直接 Edit 本檔，commit 由使用者決定）
- **空 PROBES.md 時 wrapper 跳過**：probes-daily.sh 偵測無 Active entry → 產極簡 report 「今日無 active probe」直接結束

## Output

寫到 `reports/local-analysis/{date}-probes.md`（gitignored，跟其他 4 channel local-analysis 同 dir）。
