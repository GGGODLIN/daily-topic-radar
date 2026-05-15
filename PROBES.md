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

- **Why**: 使用者直接用 CC CLI（cmux + 多 worktree + ad-hoc session + launchd 周邊 routine）、上游每次 release 都可能影響 TUI 行為、hook 機制、daemon、MCP timeout、quota、deprecation。要在「每次叫 stage-2 digest 的當下」即時 fetch、從上次看的版本 diff 到最新、surface 進 digest「🆕 CC CLI 動態」段。
- **Source(s)**: <https://github.com/anthropics/claude-code/releases>（via `gh release list anthropics/claude-code`）
- **How to fetch**: `gh release list --repo anthropics/claude-code --limit 10 --json tagName,publishedAt,name`，filter `publishedAt > Last seen`；對每個新版跑 `gh release view <tag> --repo anthropics/claude-code --json tagName,publishedAt,body` 拿 changelog 前 2000c。
- **Hit signal**: 最新 release `tagName` 比 `Last seen` 新 → 有新 release。
- **Action on hit**: stage-2 digest 開「🆕 CC CLI 動態」section、列每個新 release 的 tagName + publishedAt + body 重點 entry（按 hook / daemon / MCP / quota / deprecation / fast mode / agents 等分類）+ 每條標跟使用者 workflow 對應的個人化評論 + 顆星排序（★/★★/★★★）+ 升級指令。
- **Triggered by**: `stage-2-digest` only（**不在 06:45 daily probes 跑** — release 低頻、daily 跑會 spam；digest 階段才有差分價值）
- **Last seen**: `v2.1.142 (2026-05-14T22:55:10Z)` — 2026-05-15 digest baseline

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
