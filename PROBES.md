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
- **Last seen**: `v2.1.207 (2026-07-11T00:52:10Z)` — 2026-07-11 probe baseline（★★★ 建議關注、終端串流長 list/table/code block 凍結卡頓修復 + Deep research Fetch 階段 agent hostname 顯示修復 + plugin/hook/MCP headersHelper shell-injection 安全修復）：**v2.1.207 (2026-07-11T00:52:10Z) ★★★** 修復串流回應含超長 list / table / paragraph / code block 時終端凍結、按鍵輸入延遲的問題（★★★，直接命中日常互動流暢度）；修復 Deep research 執行時所有 Fetch 階段 agent 都顯示成「unknown」的 bug，改為顯示來源 hostname（★★★，直接命中 deep-research-paced skill 使用場景）；Plugin hooks / monitors / MCP headersHelper 的 `${user_config.*}` 在 shell-form 指令中現在會被拒絕（shell-injection 修復）：hooks 改用 exec form（`args` array）或 `$CLAUDE_PLUGIN_OPTION_<KEY>`；monitors 與 headersHelper 改在 script 內部讀值（config file 或 server 的 `env` block）（★★★，安全關鍵、命中使用者 plugin/hook 重度撰寫 + supply-chain 防禦意識）；修復非互動執行（`claude -p`、SDK）下 remote managed settings 被永久記錄為已同意、卻從未顯示安全同意對話框的漏洞（★★，命中背景自動化 / launchd routine 安全面）；修復良性系統產生的對話更新誤觸發 prompt-injection 警告的假陽性（★★，命中 prompt injection defense 規則、降低雜訊）；修復 compound command 帶 `cd` 且唯一輸出重導向到 `/dev/null` 時仍誤跳權限提示的問題（★★，命中日常 Bash tool 操作摩擦）；`extensions.worktreeConfig` 在最後一個 `worktree.sparsePaths` worktree 移除後殘留 repo `.git/config`（破壞 `tea` 等 go-git 工具）的修復（★★，命中 worktree 使用習慣）；rules glob / skill path / `.ignore` / `.worktreeinclude` 中畸形括號模式破壞檔案讀取 / 建議 / worktree 建立的修復（★★，命中 skill 撰寫 + worktree）；agent teams 隊友 mailbox 訊息格式錯誤導致每秒重複報錯 crash loop（需手動刪 mailbox 檔才能解）的修復（★★，命中 subagent-dispatch 重度使用）；背景 session 進入 git worktree 後冷重啟顯示空白的修復（★★，命中 workflow-monitor / background agent + worktree 組合）；agent view「blocked session peek」改為先顯示問題本身、附文字化 staleness clock（如「waiting 3m」）取代重複時間戳（★★，命中背景 agent 監控習慣）；Auto mode 改不再讀 repo 內 `.claude/settings.local.json` 的 `autoMode`、改讀 `~/.claude/settings.json`（★★，命中 config 撰寫階層認知）；Plugin option 值（`pluginConfigs`）不再從 project-level `.claude/settings.json` 讀取、只認 user / `--settings` / managed（★★，命中 plugin config 撰寫）；`/usage-credits` 金額輸入不再靜默把畸形值（如貼上的 timestamp）裁成數字、改報錯拒絕，超過 $1000 需打字確認（★★，命中 quota/billing 追蹤習慣）；Auto mode 在 Bedrock / Vertex AI / Foundry 免 `CLAUDE_CODE_ENABLE_AUTO_MODE` opt-in 即可用、可用 `disableAutoMode` 關閉（★，不直接相關但反映 auto mode 推廣方向）；修復 auto-updater 每次 release 都覆寫 `~/.local/bin/claude` 自訂 launcher script / symlink 的問題，`/doctor` 現在回報 externally managed launcher（★）；修復回應串流結束時 transcript 跳到答案開頭上方的顯示問題（★）；修復背景 session 若命名來自「接受 plan」不會顯示在 agent-view row 上的問題（★）；Remote Control task 狀態更新在網路中斷 / 憑證刷新後恢復時遺失的修復（★）；桌面 App 主持的 Remote Control session 在行動裝置 / 網頁端不顯示背景 agent 與 workflow 進度的修復（★）；agent view 貼上同一段文字時展開已收合的 `[Pasted text #N]` 佔位符而非新增第二個（★）；Bedrock / Vertex / Claude Platform on AWS 預設改用 Opus 4.8（★，不相關 AWS 平台）；修復 Bedrock 重複向 AWS SSO IAM Identity Center 要求新憑證的問題（★，不相關）；修復 Windows 上 AWS 憑證解析卡住（如 stuck `credential_process`）導致無限 hang、60 秒 stall guard 現在會生效（★，不相關 Windows/AWS）。前一 baseline `v2.1.206 (2026-07-10T01:45:26Z)` — 2026-07-10 probe baseline（★★★ 建議關注、MCP per-server request_timeout_ms 修復 + `/doctor` CLAUDE.md 精簡檢查）；歷史鏈 v2.1.205 / v2.1.204 / v2.1.203 / v2.1.202 / v2.1.201 / v2.1.200 / v2.1.199 / v2.1.198 / v2.1.197 / v2.1.196（背景 notification 防偽造 approval + auto mode rm -rf 安全閘 + verify skill 重複 rewrite 修復 / Sonnet 5 預設 + 1M context / Claude in Chrome GA / built-in Explore 改繼承主 session model / hook matcher 精確比對修復 / subagent 失敗如實回報等，見先前 baseline）

### ruanyf/weekly 新一期 watcher

- **Why**: 阮一峰《科技愛好者周刊》每週五出一期，內容近兩年高度集中在 AI 編程 / 模型經濟 / 軟體開發方法 / 工程師職涯，跟使用者 daily-topic-radar、interview-tour-2026、本機 AI 工具研究的選題重疊度極高（第 398 期頭條就是 OpenClaw 創辦人曬 Token 用量、Uber 燒爆 AI 預算、微軟棄用 Claude Code）。當成固定外部視角源，有新一期就在當天 digest 摘要+個人化評論。
- **Source(s)**: <https://github.com/ruanyf/weekly>（README.md 是全期索引、`docs/issue-N.md` 是各期正文）
- **How to fetch**: 用 authenticated `gh`（匿名 `curl` 打 GitHub API 會撞 rate limit 403）。取最新期號：`gh api repos/ruanyf/weekly/contents/README.md --jq '.content' | base64 -d | grep -oE 'docs/issue-[0-9]+\.md' | head -1 | grep -oE '[0-9]+'`。若 > Last seen，抓正文：`gh api repos/ruanyf/weekly/contents/docs/issue-<N>.md --jq '.content' | base64 -d`。
- **Hit signal**: 最新期號 > `Last seen` 的期號 → 有新一期。
- **Action on hit**: stage-2 digest 開「📰 科技愛好者週刊新一期」section、摘該期 12 個固定欄目（封面圖 / 本週話題社論 / 科技動態 / 文章 / 工具 / AI 相關 / 資源 / 圖片 / 文摘 / 言論 / 往年回顧）重點，標跟使用者 stack / 求職 / AI 工具研究對應的個人化評論。**正文裡的 GitHub repo 按 digest「🛠 GitHub 倉庫觀察」鐵律集中、不散落本段**。
- **Triggered by**: `stage-2-digest` only（**不在 06:45 daily probes 跑** — 週刊一週才一期、比 CC release 更低頻，daily 比對無差分價值徒增 gh call；digest 階段比對 Last seen 即可 catch）
- **Last seen**: `403（2026-07-10 发布《为什么 Dropbox 不成功》）` — 2026-07-10 probe baseline，下次 trigger = 第 404 期。内容=头条《为什么 Dropbox 不成功》— Dropbox 定位 C 端消费者工具而非企业生产力工具是根本错误（消费者只喜欢消磨时间不喜欢节省时间，只有企业才会为提高效率的软件付费），与使用者 mission-critical infra 不碰 hobby toolkit / AI coding era engineer moat 研究直接呼应；《AI 的成本超过了工程师》— Anthropic 员工计算支出是工资总额 2.3 倍（人均 51.5 万美元 AI 费用 vs 22.4 万年薪）、前1%美国软件公司人均 AI 费用均值 8.9 万/中位数 13.7 万（占工资 40%-230%），若产出不能同等增长 AI 效益为负（与 AI career landscape 研究直接呼应）；科技动态=国内无屏幕相机（透明取景窗 65g 199元）/ 英伟达向创业公司提供算力后还款（类消费贷款）/ 布朗大学 AI 作弊丑闻（闭卷考后平均分 96→48、18名学生退课）/ 欧盟 7/7 起新车强制驾驶员面部监控摄像头；文章=Elm 1.0 铁树开花（上版2019、启发 React 单向数据流）/ 面试题找出中位数 / TypeScript 7.0 正式发布（引擎 JS→Go 速度提升10倍）/ 阅读代码前先跑5条 git 命令 / x402 协议（网页付款才看内容）/ CSS 锚点定位 API（所有主流浏览器已支持）；工具栏=Vite+（统一 JS 工具单二进制，类似 meow）/ Davit（macOS Docker 容器桌面管理）/ Flint（微软+人大可视化图表引擎给 AI 调用，支持 Vega-Lite/ECharts/Chart.js）/ LaTeXSnipper（手写数学公式转 LaTeX）/ OnlyOffice Web Comp（浏览器端 Office 文件处理）/ BOSS直聘爬虫（Chrome CDP）/ EdgeEver（Cloudflare 开源笔记）/ AI Shortlink（开源短链平台）/ FlareStarter（Cloudflare Workers SaaS 起步模板）/ Rheo（Typst 转 HTML/EPUB/PDF）；AI 相关=Claude 中国用户检测工具（一键识别是否被 CC 标记中国用户，另 ClaudeFlag）/ handmux（tmux 会话搬进手机浏览器操作 CC/Codex）/ WorldCupVoice（AI 实时体育赛事解说）/ Web Cursor（浏览器 AI 编码沙箱 WebContainer）/ 文译（大模型翻译 epub 默认 DeepSeek）；资源=Awesome Zhuiju Free（免费追剧）/ 开源中文存储技术书 / 山手线声音背景音。本期提及 GitHub repo 数条（microsoft/flint-chart、SakuraMathcraft/LaTeXSnipper、electroluxcode/onlyoffice-web-comp、eatmoreduck/boss-zhipin-scraper、tianma-if/edgeever、flyfish-dev/shortlink、FlareStarter/flarestarter、gokuscraper/claude-tester、handmux/handmux、zicojiao/worldcupvoice、siuming-qiu/web-cursor、BigDawnGhost/wenyi 等）——按 digest「GitHub 倉庫觀察」鐵律應集中列出、不散落本段。前一 baseline `402（2026-07-03 发布《我在智念 AI 的日子（小说）》）` — 2026-07-04 probe baseline（头条讽刺 AI 编程职场小说、特斯拉限制员工每周 AI 支出 200 美元、工具栏 Deno Desktop/EdgeMirror/Douzy）；前一 baseline `401（2026-06-20 前后发布《如何赚到10亿美元》）` — 2026-06-26 digest 处理（PG 牛津演讲 / Speedtest 被 12 亿收购 / SQLite 作者 OSS 维护负担 / HTTP QUERY / PACT 协议）；前一 baseline `400（2026-06-12 发布《rsync 的争论》）`；前一 baseline `399（2026-06-05 发布《中国 AI 大厂访问记》）`。

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
