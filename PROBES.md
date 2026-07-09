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
- **Last seen**: `v2.1.205 (2026-07-08T21:22:06Z)` — 2026-07-09 probe baseline（★★★ 建議關注、background notification 防偽造 approval + auto mode rm -rf 安全閘 + verify skill 重複 rewrite 修復）：**v2.1.205 (2026-07-08T21:22:06Z) ★★★** 背景任務通知現在明確標示「無人類輸入發生」，防止偽造的 in-transcript approval 被執行（★★★，直接對應 unattended/launchd workflow 的 injection defense 安全根基）；auto mode 改成對無法從 context 解析的變數跑 `rm -rf` 前先問（★★★，破壞性指令安全閘）；新增 auto mode rule 阻擋竄改 session transcript 檔案（★★，安全）；auto-update binary 改 stream to disk 不再 buffer in memory、updater 峰值記憶體降約 400MB（★★）；修 project verify skills 每次 session 都被重寫、現只在 documented command 變動時才 rewrite（★★，直接對應 verify skill / update-config 機制）；修 background agent 用 SendMessage resume 後仍顯示 failed/completed（★★，影響 agent list 收尾判讀）；修 session-to-PR linking 漏掉 Bash call 輸出超過 30K inline limit 時建的 PR（★★，對應 background agent 自動 commit/push/開 PR 流程）；`/doctor` 升級成完整 setup checkup、`/checkup` 為 alias（★★）；修 `claude attach` 在背景 agent mid-upgrade restart 時 error 而非等待（★）；reserve 「Claude Browser」MCP server name（連同 「Claude Preview」）；改善 agent view（colored state word + classifier-written headline + peek full status、編輯/merge/comment/push 到既有 PR 的 session 連結 PR）；修 Windows worktree removal 在 NTFS junction/symlink 存在時刪到 worktree 外檔案；修 plugin LSP server init 失敗阻擋同副檔名 valid LSP server；修 `--json-schema` invalid schema 靜默產出非結構化輸出；修 message 在 `--max-turns` limit turn 結束時靜默丟失；修 Cowork VM-mode local-agent session 在 CLI 2.1.203+ 啟動失敗「Not logged in」。前一 baseline `v2.1.204 (2026-07-08T00:27:50Z)` — 2026-07-08 probe baseline（★★★ 建議關注、worktree 多的 repo Bash「argument list too long」修復 + worktree-isolated subagent 誤跑進 parent checkout 修復 + background session 掉失 `ANTHROPIC_BASE_URL` 誤打 default endpoint 401 修復，三項直接打中 git worktree 高頻工作模式 + vendor endpoint routing；另含 SessionStart hook streaming 修復）；歷史鏈 v2.1.203 / v2.1.202 / v2.1.201 / v2.1.200 / v2.1.199 / v2.1.198 / v2.1.197 / v2.1.196 / v2.1.195（Sonnet 5 預設 + 1M context、Claude in Chrome GA、built-in Explore 改繼承主 session model、hook matcher 精確比對修復、subagent 失敗如實回報等，見先前 baseline）

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
