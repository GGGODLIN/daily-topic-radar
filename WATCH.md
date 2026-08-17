# Watch list — 被動 match（stage-2 digest 階段）

> 手動 maintain 的 watch list。每次跑 stage-2 digest 時 grep 當天 raw md 看有沒有 hit，有就 surface 進 digest「系統當天動態」or「興趣命中」段。
>
> 被動 = 不主動拉外部 source，靠既有 raw md（reddit / HN / blog / RSS）自然含的訊號比對。主動拉的東西放 `PROBES.md`。
>
> 兩個 section：`## Active Issues`（GitHub bug state machine、closed 才 surface）/ `## Watched Topics`（議題 / 動向、hit 直接 highlight）。

## Active Issues

_(空 — #55938 已於 2026-06-12 CLOSED 移至 ## Resolved)_

## Watched Topics

### AI 工具限時優惠／名額

- **Why I care**: 使用者自費訂 AI 開發工具，限時降價 / 促銷波 / 名額釋出的可複製窗口常只有幾小時——2026-08-17 SuperGrok Heavy $300→$99 那波就是隔天早上才在 Threads 看到（晚約 10 小時）、已無法複製，而當天 digest 對這題**零命中**（不是晚報，是根本沒掃）。這條 entry 補的就是那個零命中：第一次見到的優惠靠 digest 被動掃出來，不必先為它維護 watchlist entry（已知標的的每小時輪詢是另一條產線的事）。
- **Match keywords**: `(\$[0-9]+[[:space:]]*(->|→|to)[[:space:]]*\$[0-9]+|[0-9]{1,3}% ?(off|discount)|(price|pricing)[ -](drop|cut|slash(ed)?)|half[ -]price|promo (code|wave)|discount code|coupon|limited[ -]time|for a limited time|(free|bonus|promo)[ -](credits?|month|tier|trial)|(waitlist|invite[ -]?code|early access)[ -]?(open|opened|available)|(seats?|spots?|slots?|quota)[ -]?(available|left|remaining|open|bump|increase)|限時|優惠|折扣|特價|降價|半價|早鳥|免費(額度|方案|試用|升級)|(名額|額度)(開放|釋出|限量)|(邀請|折扣|優惠|兌換)碼)`（**全域** grep raw md、不套 CC scope — 訊號散在各家 AI 工具社群，限縮 Claude Code / claude-code 會全漏）
- **Action on hit**: surface 進 digest「興趣命中」段，按「具體可行動內容」門檻分級 — 有操作步驟 / 連結 / 成功回報的排前面，並標可複製窗口（生效期限、名額數、代碼、原文連結）；只有提及、沒有做法的壓一行帶過。這是給使用者判斷用的浮現機制，agent 不代為註冊 / 兌換 / 下單，要細節由使用者點名再展開原文。
- **Added**: 2026-08-17

### Entry 範本

```
### <title — 議題名稱>

- **Why I care**: <一句話 context — surface 時 agent 引用>
- **Match keywords**: `<regex>`（在 CC scope 或全域 grep raw md）
- **Scope filter**（optional）: `Claude Code|claude-code` 之類，限縮上下文
- **Action on hit**: <命中時做什麼 — surface 進 digest 哪段 / 標 priority / 想抓細節 follow up>
- **Added**（optional）: <YYYY-MM-DD，entry 加入日期。填了的話，加入起 3 個曆日內 surface 時 digest 會自動掛「⚠️ 新來源待驗」標記；不填 = 視為老來源、不標記>
```

Watched topic 跟 Active issue 差別：

- Issue 走兩階段（grep + gh confirm），只在 state=CLOSED 才 surface
- Topic **任何 hit 都 surface**（你想看到的就是這個議題出現的訊號本身）

## Resolved

### anthropics/claude-code#55938 — Wide-table fallback leaves stale bordered paint in scroll buffer alongside the key-value re-render

- **Opened**: 2026-05-04 by ofcRS (Aleksandr Sakhatskii)
- **Closed**: 2026-06-12（closedAt 2026-06-12T11:24:17Z；2026-06-13 stage-2 digest WATCH attempt 在 Stage 1 raw md 無命中，但例行 gh confirm 抓到 state=CLOSED — 跟 CC v2.1.174–176 三連發同日，極可能其中一版帶了修復、release notes 未明寫）
- **URL**: <https://github.com/anthropics/claude-code/issues/55938>
- **What it was**: #44696 wide-table fallback 觸發後 scroll buffer 殘留舊表格邊框、視覺混亂；同一個 markdown table 在 scroll history 出現兩次（先壞掉的 bordered ASCII、後 stacked key-value cards）。實證環境 CC v2.1.126 / Alacritty / macOS
- **Resolution applied (2026-06-13)**: 升 CC v2.1.174+ 後驗證寬表格輸出不再 paint stale border；副作用 issue 跟父 bug #44696 同方向修復告一段落，整個 wide-table fallback feature 線收尾

### anthropics/claude-code#44696 — Wide markdown tables collapse into stacked key-value cards instead of rendering as tables

- **Opened**: 2026-04-07 by waihonger (Wai Hong Fong)
- **Closed**: 2026-05-14（closedAt 2026-05-14T21:28:19Z；2026-05-30 stage-2 digest Stage 1 grep 命中 → `gh issue view` 確認 CLOSED）
- **URL**: <https://github.com/anthropics/claude-code/issues/44696>
- **What it was**: CC TUI 80-cols terminal 寬時 markdown table 強制降級成 key-value card 列表（每 entry `────` 分隔）。實證環境 CC v2.1.140 / cmux / 80 cols
- **Resolution applied (2026-05-30)**: 已移除 `~/.claude/CLAUDE.md`「Markdown 表格」整段 + memory `feedback_prefer_tables_over_bullets.md` 2026-05-13 update 段，恢復「視覺體感」原則為主、不嚴格壓縮 cell。副作用 issue #55938（stale border paint）仍 OPEN、留 ## Active 監控

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
