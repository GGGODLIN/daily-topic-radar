# Watch list — 被動 match（stage-2 digest 階段）

> 手動 maintain 的 watch list。每次跑 stage-2 digest 時 grep 當天 raw md 看有沒有 hit，有就 surface 進 digest「系統當天動態」or「興趣命中」段。
>
> 被動 = 不主動拉外部 source，靠既有 raw md（reddit / HN / blog / RSS）自然含的訊號比對。主動拉的東西放 `PROBES.md`。
>
> 兩個 section：`## Active Issues`（GitHub bug state machine、closed 才 surface）/ `## Watched Topics`（議題 / 動向、hit 直接 highlight）。

## Active Issues

### anthropics/claude-code#44696 — Wide markdown tables collapse into stacked key-value cards instead of rendering as tables

- **Opened**: 2026-04-07 by waihonger (Wai Hong Fong)
- **State**: OPEN（last updated 2026-05-13 03:51）
- **URL**: <https://github.com/anthropics/claude-code/issues/44696>
- **Match keywords**: `table|markdown.*table|key-value|wide.*table|stacked.*card`（grep 配對 CC scope 內）
- **Impact on me**: CC TUI 80-cols terminal 寬時 markdown table 強制降級成「每 entry 一個 section + ──── 分隔線」key-value card 列表。實證環境 CC v2.1.140 / cmux / 80 cols / Opus 4.7 1M context（2026-05-13）
- **Workaround**: Response 時主動寫短 cell 表 + 長動作換段落。**實測 hard rule**（T1-T13 binary search 13 tests, 2026-05-13）：row display width ≤ 120 chars (中文字符算 2) → keep；≥ 142 chars 必降級。Rule 已寫進 `~/.claude/CLAUDE.md` 「Markdown 表格」段
- **When closed**: 移除 `~/.claude/CLAUDE.md` 「Markdown 表格」整段 + memory `feedback_prefer_tables_over_bullets.md` 2026-05-13 update 段；恢復「視覺體感」原則為主、不嚴格壓縮 cell

### anthropics/claude-code#55938 — Wide-table fallback leaves stale bordered paint in scroll buffer alongside the key-value re-render

- **Opened**: 2026-05-04 by ofcRS (Aleksandr Sakhatskii)
- **State**: OPEN（last updated 2026-05-12）
- **URL**: <https://github.com/anthropics/claude-code/issues/55938>
- **Match keywords**: `scroll.*buffer|stale.*border|wide.*table.*fallback|paint`（grep 配對 CC scope 內）
- **Impact on me**: #44696 fallback 觸發後 scroll buffer 殘留舊表格邊框、視覺混亂。比 #44696 影響輕、但同一條 fix path
- **Related to**: #44696（同一個 wide-table fallback feature 的副作用 bug）

## Watched Topics

_(空 — 使用者之後 append 感興趣的議題 / 動向 entry)_

### Entry 範本

```
### <title — 議題名稱>

- **Why I care**: <一句話 context — surface 時 agent 引用>
- **Match keywords**: `<regex>`（在 CC scope 或全域 grep raw md）
- **Scope filter**（optional）: `Claude Code|claude-code` 之類，限縮上下文
- **Action on hit**: <命中時做什麼 — surface 進 digest 哪段 / 標 priority / 想抓細節 follow up>
```

Watched topic 跟 Active issue 差別：

- Issue 走兩階段（grep + gh confirm），只在 state=CLOSED 才 surface
- Topic **任何 hit 都 surface**（你想看到的就是這個議題出現的訊號本身）

## Resolved

_(空)_

## Check protocol（two-stage：raw md 訊號當低成本 filter，gh 確認）

每次跑 stage-2 digest 走兩階段：

### Stage 1 — grep raw md 找候選訊號

每個 entry 的 `Match keywords` 在 **CC scope 範圍內** grep `reports/{date}.md`，避免 false positive（「table」字會 hit benchmark 帖等無關內容）：

```bash
DATE=$(date +%Y-%m-%d)
# 兩階段 grep：先框 CC scope（前後 5 行），再用 entry keyword 過濾
grep -in -B2 -A5 "Claude Code\|claude-code" reports/${DATE}.md | grep -iE "<entry's match keywords>"
```

有 hit → 進 Stage 2。
沒 hit → 該 entry 今天不查 gh，digest 不寫 watch 段。

### Stage 2 — gh 確認 state

```bash
gh issue view <num> --repo <owner>/<repo> --json state,closed,closedAt,updatedAt
```

- **`state=CLOSED`** → surface 進 digest「系統當天動態」段；使用者確認後把 entry 從 ## Active 移到 ## Resolved，補 `Closed: YYYY-MM-DD (PR link)`
- **`state=OPEN` 但 `updatedAt` 變動** → 看 thread 新訊號（label / milestone / Anthropic 回應）surface

### 週度 fallback

每週二 codemap weekly 跑那天，對所有 Active entry 跑一次 `gh issue view` 做同步、避免漏掉沒被社群討論的靜默 close。

### 新增 entry 必填欄位

每個 Active entry 必須帶：
- title (issue 原 title 不譯)
- Opened / State / URL（基本 metadata）
- **Match keywords**（regex，用於 Stage 1 grep；想得越精準 false positive 越少）
- Impact on me（中文，跟本人 workflow 的具體關連）
- Workaround（短期應對）
- When closed（一句話寫清「修了之後我恢復做什麼」）

## 維護規則

- **新增 entry**：只放「上游修了會直接改變我 daily routine / response style」的 bug，不放泛泛 feature request
- **不寫已 closed 的歷史**：closed → 移 ## Resolved → 一年內保留 → 再刪
- **schema 保持簡單**：title 抄 issue 原 title（不譯）；impact 用中文寫清楚跟本人 workflow 的具體關連
