# Probes — 主動拉外部訊號（stage-1 / daily fetch 階段）

> 手動 maintain 的「需要主動去查、不能等被動社群討論浮上來」的源。Agent 跑時讀此檔，對每個 active probe 跑對應工具 fetch、結果寫進 `reports/local-analysis/{date}-probes.md`。
>
> 主動 = 直接拉外部 source（GitHub API / RSS / web）。被動比對既有 raw md 的東西放 `WATCH.md`。
>
> **觸發**：
> - **自動**：launchd `com.gggodlin.local-analysis-probes` 每日 06:45（在 4 channel 之後）
> - **手動**：使用者說「跑 probes」/「probes 一下」/「拉一下 probes」→ agent 讀 PROBES.md 跑

## Active

_(空 — 使用者之後 append 具體 probe entry)_

### Entry 範本

```
### <title — 簡短 probe 名>

- **Why**: <為什麼要 probe — context，之後 agent 整理結果時引用>
- **Source(s)**: <去哪查 — URL / repo / API endpoint>
- **How to fetch**: <agent 用什麼工具：`gh release view <owner>/<repo>` / WebFetch / `curl` JSON / Context7 / claude-in-chrome MCP>
- **Hit signal**: <什麼算「有新東西」— version > X / 上次 fetch 時間 / 特定 keyword 出現>
- **Action on hit**: <找到後做什麼 — inject 進當天 digest「外部訊號」段、標 priority、想抓細節 follow up>
- **Last seen**（optional）: <上次 baseline 值 — agent 每次 fetch 完更新這欄；空白代表第一次跑>
```

## Resolved / Archived

_(probe 不再 relevant 時移到這、保留歷史、一年後刪)_

## 維護規則

- **Hit signal 必填**：沒寫判準 agent 不知道 baseline 在哪、會每天 report 同樣內容；最簡單寫「上次 fetch 時間 / version / hash」
- **How to fetch 寫具體命令或工具**：不要寫泛泛「查 GitHub」，寫 `gh release view <owner>/<repo> --json tagName,publishedAt`，agent 才能照跑
- **Last seen 由 agent 更新**：跑完 probe 後若 hit signal 達標，agent 把新 baseline 寫回對應 entry 的 `Last seen` 欄（直接 Edit 本檔，commit 由使用者決定）
- **空 PROBES.md 時 wrapper 跳過**：probes-daily.sh 偵測無 Active entry → 產極簡 report 「今日無 active probe」直接結束

## Output

寫到 `reports/local-analysis/{date}-probes.md`（gitignored，跟其他 4 channel local-analysis 同 dir）。
