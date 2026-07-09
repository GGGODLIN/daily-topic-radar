# Wiki 自動化設計 — 接到 local-analysis sibling 體系

**Date**: 2026-06-15
**Status**: Design (pending user review)
**Affected paths**:
- `~/code/social-info/scripts/local-analysis/` (4 新 sibling + 1 rename)
- `~/code/social-info/reports/local-analysis/` (5 個 wiki-* 命名統一)
- `~/.claude/workflows/local-analysis.js` (CHANNELS array 改動)
- `~/.claude/wiki/_schema.md` (schema 補充)
- `~/.claude/commands/wiki-actions.md` (新 slash command)
- `~/Desktop/projects/.claude/trials/active.md` (4 entry trial-review)

---

## TL;DR

把 wiki 自動化痛點（lint / cross-link / graduation / stale）拆成 4 個 sibling shell script，接到既有 `~/code/social-info/scripts/local-analysis/` 體系（已有 11 sibling）。User morning 跑 `/daily-local` 時自動產 5 個 wiki-* report 進 digest。Agent 主動推薦 action 但全 read-only，唯一寫入端是新 `/wiki-actions` slash command。

---

## 背景

### Reframe：拆開 memory 跟 wiki 治理

過去否決「自動蒸餾」立場（[[reference_mempalace_read_write_imbalance]] + 14 次同槽否決紀錄整理在 [[memory-consolidation-rejection-landscape]]）。Reframe 為「**記憶層仍 human-gate，wiki 層可彈性自動化**」後重新評估：

- **Memory（research-brain）**：人工 gate 立場不變（行為 drift cost 高）
- **Wiki（entity-centric 知識庫）**：拆開後可彈性，但仍維持 propose vs auto-write 紅線

### 痛點清單

User reframe 後盤點的 wiki 流程痛點（已選的）：

| 痛點 | 現況 | 本設計處理 |
|---|---|---|
| A. Index.md 同步漂移 | 62 (index) vs 65 (實際 entity) 漂移 | `/wiki-actions` 寫入時同步 |
| B. Cross-link `[[name]]` 維護 | 手動 graft、漏連 | wiki-cross-link sibling propose |
| C. Lifecycle 升級遺漏 | 靠記憶 | wiki-lint 偵測 frontmatter 缺欄位 |
| D. Graduation 評估（→ CLAUDE.md rule）| 沒主動回看 | wiki-graduation sibling |
| E. Stale 偵測（`stale-by:` 過期）| 沒主動提醒 | wiki-stale sibling |
| F. Memory ↔ Wiki 同步漂移 | — | **本設計不做**（user 確認 cluster 跟 wiki 漂移是設計） |
| G. 候選提案（cluster 成熟 → propose entity）| `wiki-candidates-daily.sh` 已 cover | 不動 |
| H. 內容草擬（raw doc → wiki）| `/wiki-promote` 從 memory 蒸餾 | **本設計不做**（user 確認無 raw doc → wiki 需求） |

### 為什麼最終不採用任何外部套件

走過 deep-research + Tier 1 candidate 深查（kfchou/wiki-skills 153★ / sametbrr/llm-wiki-manager 41★ / NiharShrotri 39★ / hellohejinyu 55★）後決定不採用：

1. **既有 local-analysis 體系已有 11 sibling 跑 daily LLM analysis**——4 個 wiki sibling 接進去 cost 最低
2. **`/wiki-promote` 已 cover memory → wiki promotion**——保留不動
3. **`wiki-candidates-daily.sh` 已 cover 痛點 G**——不重複造
4. **純 shell + heredoc PROMPT pattern** 跟 user muscle memory 一致（11 sibling 同 pattern）
5. **Agent 友善度三軸**（結構對齊 / 接觸面 / 設計乾淨）下，自寫 4 個 sibling 比裝外部 plugin（即使 kfchou）勝出
6. **遵守 [[feedback_daily_automation_inventory_first]]**：inventory-first 發現 sibling pattern 直接 reuse

### Agent-only workflow 下的評估軸修正

過程中關鍵 reframe：「妥協 Obsidian」對 agent-only workflow cost ≈ 0（純 md 加 `.obsidian/` 目錄即 vault，不裝 Obsidian app）。真實軸是「**套件 convention 對齊 + 接觸面 + 設計乾淨**」而非「綁不綁 Obsidian」。**Stars 在 agent-only context 不是主要訊號**（反映人類 UI adoption）。

---

## 設計

### 1. 檔案組織（保守路線：平面 + 命名前綴 grouping）

```
~/code/social-info/scripts/local-analysis/
  ...既有 11 sibling...
  wiki-candidates-daily.sh    ← 既有（G）
  wiki-cross-link-daily.sh    ← 新（B）
  wiki-graduation-daily.sh    ← 新（D）
  wiki-lint-daily.sh          ← 新（A/B/C）
  wiki-stale-daily.sh         ← 新（E）
```

不建 `wiki/` subdir、維持平面。5 個 `wiki-*` filename 排序時自然連續。

Reports 命名統一前綴：

```
~/code/social-info/reports/local-analysis/
  $DATE-wiki-candidates.md    ← 既有改名（從 $DATE-wiki.md）
  $DATE-wiki-cross-link.md    ← 新
  $DATE-wiki-graduation.md    ← 新
  $DATE-wiki-lint.md          ← 新
  $DATE-wiki-stale.md         ← 新
```

⚠️ **連動改 `wiki-candidates-daily.sh` 內 OUT path**：`OUT="$OUT_DIR/$DATE-wiki.md"` → `OUT="$OUT_DIR/$DATE-wiki-candidates.md"`。歷史 `$DATE-wiki.md` 留著不動。

### 2. 4 個新 sibling 規格

每個 wrapper 100% 仿 `wiki-candidates-daily.sh` 結構：

```bash
#!/bin/bash
cd /
set -euo pipefail
PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/linhancheng/.local/bin"
export PATH

CLAUDE="/Users/linhancheng/.local/bin/claude"
REPO_DIR="/Users/linhancheng/code/social-info"
OUT_DIR="$REPO_DIR/reports/local-analysis"
LOG_DIR="$REPO_DIR/logs"

mkdir -p "$OUT_DIR" "$LOG_DIR"
DATE=$(date +%Y-%m-%d)
OUT="$OUT_DIR/$DATE-wiki-<aspect>.md"
LOG="$LOG_DIR/local-analysis-wiki-<aspect>-$DATE.log"

cd "$REPO_DIR"

PROMPT=$(cat <<'EOF'
（依 sibling 不同的判準 + 6 段結構 + recommendation block 細節）
EOF
)

{
  echo "=== wiki-<aspect> started: $(date) ==="
  "$CLAUDE" -p "$PROMPT" > "$OUT" 2>&1
  size=$(wc -c < "$OUT")
  echo "=== wiki-<aspect> finished: $(date) ==="
  echo "Output: $OUT ($size bytes)"
  if [ "$size" -lt 500 ]; then
    echo "⚠️  WARNING: output < 500 bytes ($size), agent likely short-circuited"
    cat "$OUT"
  fi
} >> "$LOG" 2>&1
```

#### 2.1 `wiki-lint-daily.sh`

**掃**：`~/.claude/wiki/*.md` 自身結構

**判準**：
1. **Orphan**：entity 沒被任何其他 wiki entity 或 memory cluster 引用（`grep -r "[[<slug>]]"` in `~/.claude/wiki/` + `~/.claude/memory/` 共 0 命中）
2. **Broken `[[]]`**：entity 內文有 `[[xxx]]` 但 `~/.claude/wiki/xxx.md` 不存在
3. **Frontmatter 缺欄位**：缺 `topic` / `last_updated` / `confidence` / `lifecycle` / `sources` 任一
4. **重複 entity**：兩個 entity TL;DR 相似度高（同義內容散在兩檔）

**6 段結構**：
```
## 掃描範圍
## 前一日 follow-up
## 🔴 Broken links (must fix)
## 🟡 Orphan pages (可能該 cross-link 或刪除)
## 🟢 Frontmatter 缺欄位 (補就好)
## ⚠️ 可能重複 entity (建議合併)
## 🎯 今日推薦 actions (按 cost-benefit 排序)
```

#### 2.2 `wiki-cross-link-daily.sh`

**掃**：`~/.claude/wiki/*.md` 內文 + `~/.claude/memory/**/*.md` 引用

**判準**：
1. **平文 mention 沒 wikilink**：wiki entity A 內文出現 "mempalace"（純字）但沒包 `[[mempalace]]`
2. **記憶 cluster 引用漂移**：memory cluster 用 `[[name]]` 引用某 wiki entity，但那個 entity 沒 backlink 回 cluster
3. **新 entity 加入後沒 backlink**：最近 7 天加的 entity，其他既有 entity 沒任何引用回它

**False positive 防護**：entity slug 短於 5 字元或屬於常用詞清單（如 `wiki`、`memory`、`hook`、`skill`、`agent`、`tool`、`config`、`plan` 等）→ 不 propose。

**6 段結構**：
```
## 掃描範圍
## 前一日 follow-up
## 🔗 平文 mention 該補 wikilink
## ↔️ Backlink 漂移
## 🆕 新 entity 待 cross-link
## 已掃但結構正常
## 🎯 今日推薦 actions
```

#### 2.3 `wiki-graduation-daily.sh`

**掃**：wiki entity frontmatter + cluster 引用次數

**判準**（4 條全滿足才列升級候選）：
1. `confidence: high`
2. `lifecycle: verified`
3. Changelog 近 1 個月無大改動（最後 changelog entry > 30 天）
4. 跨 cluster / standalone 引用 ≥ 3（grep `[[<slug>]]` in `~/.claude/memory/**/*.md` 統計）

**升級目標**：promote 成 `~/.claude/CLAUDE.md` rule 或 `~/.claude/rules/` 條目。

**6 段結構**：
```
## 掃描範圍
## 前一日 follow-up
## 🎓 升級候選 (4 條全滿足)
## ⏸ 接近成熟 (滿足 3/4 條)
## 已升級紀錄 (snapshot, 從 _schema.md 反向降級段反查)
## 已掃但不夠成熟
## 🎯 今日推薦 actions
```

#### 2.4 `wiki-stale-daily.sh`

**掃**：wiki entity frontmatter + Changelog

**判準**（2026-06-15 prerequisite A 修正：原判準 1 `stale-by:` 過期被拿掉因 65 entity 0 用 `stale-by:` 欄位）：
1. **`lifecycle: stale` 已 N 天**：lifecycle 標為 stale，距 last_updated > 30 天沒 refresh
2. **久未 changelog**：confidence 不是 stale 但 last changelog > 90 天且 lifecycle 不是 verified

**6 段結構**：
```
## 掃描範圍
## 前一日 follow-up
## 💤 Lifecycle:stale 久未 refresh (建議 archive 或 promote 進 CLAUDE.md 後刪除)
## 📜 久未 changelog 但非 verified (建議升 lifecycle 或補 changelog)
## ⚠️ Parse 失敗 (frontmatter 解析錯)
## 已掃但 fresh
## 🎯 今日推薦 actions
```

### 3. Recommendation block 機制（每 finding 必帶）

格式範例（broken link case）：

```markdown
### 🔴 Broken link: [[mempallace]] in fact-check-protocol.md

- **在哪**：~/.claude/wiki/fact-check-protocol.md:42
- **為什麼**：[[mempallace]] 指到不存在的 entity（疑似 typo of mempalace）
- **建議 action**（選一）：
  - A. 修正 typo: `[[mempallace]]` → `[[mempalace]]`
  - B. 標 `wiki_lint.broken_link_mempallace: declined`（內文真的指另一個東西）
- **One-liner**:
  \`\`\`bash
  sed -i '' 's/\[\[mempallace\]\]/\[\[mempalace\]\]/g' ~/.claude/wiki/fact-check-protocol.md
  \`\`\`
- **預估 cost**：30 秒
- **Confidence**：high
```

每個 finding 必帶 5 個欄位：
- 建議 action（2-3 個選項）
- One-liner CLI（或編輯路徑 + 行號）
- 預估 cost（值得做的時間估）
- Recommendation confidence（agent 對推薦的信心 high/medium/low）

### 4. Action queue（每個 sibling 報告末尾）

按 cost-benefit 優先級排序的扁平 list：

```markdown
## 🎯 今日推薦 actions (按 cost-benefit 排序)

1. **[HIGH] 修 [[mempallace]] → [[mempalace]] in fact-check-protocol.md** (~30 秒, conf:high)
   → \`sed -i '' '...' ~/.claude/wiki/fact-check-protocol.md\`

2. **[MED] 重驗 [[mempalace]] confidence** (~15 分鐘, conf:med)
   → 看 mempalace 退役後 14 天狀態，決定 confidence 升降 + 更新 last_updated

3. **[LOW] 補 [[fact-check-protocol]] 4 處 wikilink** (~2 分鐘, conf:med)
   → 在 4 個檔的指定行加 \`[[]]\` 包覆
```

### 5. Decline / hold / override 機制（仿 `wiki_promote:`）

User 拍板拒絕後寫進 wiki entity frontmatter，sibling 下次掃自動跳過：

```yaml
---
topic: fact-check-protocol
last_updated: 2026-06-15
# ...既有欄位...
wiki_lint:
  orphan: declined                  # 確認 orphan 但保留
  broken_link_xxx: declined         # 連結壞但意圖如此
wiki_cross_link:
  mempalace-mempalace: declined     # 不要 propose 這個 pair
wiki_stale:
  override_until: 2026-09-01        # 過期但這日期前不要 nudge
wiki_graduation:
  hold-until-cluster-stable         # graduation 候選但等 cluster 穩定
---
```

Sibling 開頭讀 `wiki_<category>:` 段：
- `declined`：完全跳過、不列任何段
- `hold-*`：列「⏸ HOLD」段、不催促、不累計久懸天數
- `override_until: YYYY-MM-DD`：在這日期前跳過、過期重新 surface

### 6. Dispatcher 接線（`~/.claude/workflows/local-analysis.js`）

`CHANNELS` array 改動：

```javascript
const CHANNELS = [
  { key: 'memory', freq: 'daily', kind: 'llm', src: '/Users/linhancheng/.claude/commands/memory-audit.md' },
  // rename: 'wiki' → 'wiki-candidates'
  { key: 'wiki-candidates', freq: 'daily', kind: 'llm', src: `${W}/wiki-candidates-daily.sh` },
  // 新增 4 個
  { key: 'wiki-lint', freq: 'daily', kind: 'llm', src: `${W}/wiki-lint-daily.sh` },
  { key: 'wiki-cross-link', freq: 'daily', kind: 'llm', src: `${W}/wiki-cross-link-daily.sh` },
  { key: 'wiki-graduation', freq: 'daily', kind: 'llm', src: `${W}/wiki-graduation-daily.sh` },
  { key: 'wiki-stale', freq: 'daily', kind: 'llm', src: `${W}/wiki-stale-daily.sh` },
  // ...其他既有 channel 不動...
]
```

不動 dispatcher 邏輯（`isDue` / `llmPrompt` / `shellPrompt` / `parallel` / `digest` 階段全 reuse）。

### 7. `/wiki-actions` slash command（唯一寫入端）

`~/.claude/commands/wiki-actions.md`：

```markdown
---
description: 讀今日 wiki-* 5 個 report、合併 action queue、user 拍板後執行
---

讀今日 wiki-* 5 個 report：
- \`cat ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-{candidates,lint,cross-link,graduation,stale}.md\`

抽各自「🎯 今日推薦 actions」段，**按 HIGH/MED/LOW 排序合併**成單一 list。
**同 entity 多 sibling propose 時 group by entity 顯示**，user 一次看完該 entity 所有 action 拍板。

報告給 user 後等拍板：
- 「全部做」/「做 1, 3, 5」/「全部 hold 到 X 日期」/「跳過」

對批准的 action：
- 有 one-liner 的 → bash 跑
- 沒 one-liner 的 → 跟 user 確認手動步驟

對 hold / declined：
- 寫 wiki entity frontmatter（仿 `wiki_promote: declined/hold-*` 格式）
- 例如 `wiki_stale.override_until: 2026-09-01`

完成後 summary：執行了 X 個 / hold Y 個 / declined Z 個。

紀律：
- **唯一寫入端**（4 sibling + wiki-candidates 全 read-only）
- 寫入前列 diff 給 user 看
- 永遠 user-triggered（不放 hook、不放 cron）
```

### 8. Schema 補充（改 `~/.claude/wiki/_schema.md`）

**Propose-level 標記段（仿既有 `wiki_promote:`）**

加進 `_schema.md` 的「Entity page schema」段（不加 `stale-by:` — prerequisite A 顯示 0 entity 用、移除 spec 假設）：

```yaml
# user 拍板拒絕後寫進來，sibling 下次掃自動跳過
wiki_lint:
  <finding-key>: declined          # 如 orphan: declined
wiki_cross_link:
  <entity-pair>: declined          # 如 mempalace-mempalace: declined
wiki_graduation:
  hold-<reason>                    # 如 hold-until-cluster-stable
wiki_stale:
  override_until: YYYY-MM-DD       # lifecycle:stale 但這日期前不要 nudge
```

### 9. 細部實作 spec（補偷工：PROMPT heredoc / 合併演算法 / one-liner 安全）

#### 9.1 PROMPT heredoc 共同 template

每個 sibling 的 PROMPT heredoc 內容由 4 段組成（仿 `wiki-candidates-daily.sh` 的內部結構）：

```
[1] Role + 任務描述（1 段）
    「你是 wiki-<aspect> audit agent。任務：掃 <source> 找 <pattern> 產 markdown 報告」

[2] 第一原則：永遠輸出完整 6 段 markdown（複製 wiki-candidates 第 18-51 行的紀律段）
    - 6 段標題逐字照抄
    - < 500 bytes 視為 short-circuit failure
    - 禁止 single line skip / preamble / code fence wrap

[3] Promote-status 標記處理段（用 wiki-candidates 第 60-66 行為基礎、改成 wiki_<aspect>:）
    - 讀 entity frontmatter wiki_<aspect>: 段
    - declined / hold-* / override_until 處理
    - 注意：不同 sibling 對 declined 的 key 解析不同（lint 是 finding-key、cross-link 是 entity-pair、stale 是 override_until 日期、graduation 是 hold-* 字串）

[4] 判準段（這段 sibling 各自不同，依 spec 2.1-2.4）
    + 每個 finding 必帶 recommendation block 5 欄位（spec 3）
    + 末段「🎯 今日推薦 actions」按 cost-benefit 排序（spec 4）

[5] 前一日 follow-up（複製 wiki-candidates 第 84-93 行）
    + 改 report filename pattern 為 $DATE-wiki-<aspect>.md
    + 對每個前日 finding：檢查問題是否還在 → 標 ✅ 解決 / carry forward

[6] 紀律段（嚴格 read-only / 不執行寫操作 / 6 段強制 / 第一個 byte 必是 ## 掃描範圍）
```

實作時：4 個 sibling 共用段 [2][5][6]（可抽 fragment 後續 reuse），段 [1][3][4] 依 sibling 客製。

**寫 wrapper 時的 token budget**：每個 sibling 預估 PROMPT 內容 ~3-5K bytes（仿 wiki-candidates 5K bytes 規模）。

#### 9.2 `/wiki-actions` 合併演算法（明確規格）

讀 5 個 report 的「🎯 今日推薦 actions」段後合併 logic：

```
1. Parse 每個 report 的 action queue → 抽出 actions list
   每個 action: { sibling, priority (HIGH/MED/LOW), entity, action_desc, one_liner, cost, confidence }

2. Group by entity（同 entity 多 sibling propose 合併）
   - Group key: entity slug
   - 若 entity 出現在 ≥ 2 個 sibling 的 action queue：合併成 entity-level entry
     顯示時列「⚠️ <entity> 被 N 個 sibling 同時 surface」+ 列各 action

3. Priority 排序規則（明確）
   - Entity-level priority = MAX(各 sibling 對該 entity 的 priority)
     即：lint 標 HIGH + stale 標 LOW → entity-level 為 HIGH
   - 同 priority 內按 confidence 排（high > medium > low）
   - 同 confidence 內按 cost 升序（30 秒先於 15 分鐘）

4. 輸出格式給 user
   - 按 entity-level priority 順序列
   - 每 entity 展開列各 sibling 的 individual action（user 可逐個 approve / decline）

5. 拍板介面（user 回應 parse）
   - 「全部做」→ approve 全部 individual actions
   - 「做 entity X, Y」→ approve 該 entity 所有 actions
   - 「做 1, 3, 5」→ 按 flat list 序號 approve（系統需顯示序號）
   - 「跳過 entity X」→ decline X 所有 actions
   - 「hold entity X 到 YYYY-MM-DD」→ 寫 override_until 到 X frontmatter
```

#### 9.3 One-liner 安全機制

LLM 產出 one-liner 可能 destructive。`/wiki-actions` 跑前必過 4 道 guard：

**Guard 1：黑名單 pattern detection**

跑前 grep one-liner 字串、命中以下任一 → 標「⚠️ DESTRUCTIVE: 拒跑、要 user 手動」：

```
rm -rf / rm -r / rm \(no -i\)
> \(redirect overwrite without &>>\)
truncate
dd of=
chmod 7\d\d / chmod -R
git reset --hard / git push --force / git clean -fd
mv ~/ <target outside ~/.claude/wiki/ or ~/code/social-info/>
```

**Guard 2：Path scope check**

One-liner 操作的檔案路徑必須限定在：
- `~/.claude/wiki/*.md`（wiki entity）
- `~/code/social-info/scripts/local-analysis/*.sh`（sibling wrapper）
- `~/code/social-info/reports/local-analysis/*.md`（report）

超出範圍 → 拒跑。

**Guard 3：Dry-run 預設**

`/wiki-actions` 跑 one-liner 預設模式：

```bash
# 對 sed 類：先 print diff
sed -n 's/.../.../p' <file>    # show what would change
# 用 git diff 比 (entity 是 git tracked)
sed -i.bak '...' <file> && diff <file>.bak <file>
```

User 確認 diff 後才 commit。

**Guard 4：Backup**

執行寫入前先 `cp <file> <file>.wikiactions.bak.<timestamp>`，跑完 user 確認沒問題才刪 backup。

**Fallback**：以上 4 道 guard 任一觸發 → `/wiki-actions` 列「⚠️ Guard X 觸發，這條 action 改成手動指引」+ 給 user 文字說明該手動做什麼。

---

## Data flow

```
user morning 跑 /daily-local
  ↓
local-analysis.js workflow start
  ↓
Phase 'Scan': parallel 跑所有 due channel (10+ 含 5 個 wiki-*)
  ↓
LLM agent 讀各 wrapper 的 PROMPT 內容（不執行 wrapper）
  自己跑分析 + Bash 寫 report 到 $DATE-<key>.md
  ↓
5 個 wiki-* report 落 reports/local-analysis/
  ↓
Phase 'Digest': agent 合成所有 channel summary
  5 個 wiki-* summary 在 digest 連續顯示
  末段「今天值得做的 1-2 件事」（既有 digest agent）
  ↓
digest return → user 看
  ↓
user 跑 /wiki-actions
  ↓
讀今日 5 個 wiki-* report → 合併 action queue → user 拍板
  ↓
執行批准的 one-liner / 寫 declined-hold-override 到 frontmatter
```

---

## Error handling

| 情境 | 處理 |
|---|---|
| `claude -p` 超時 / 失敗 | 既有 `< 500 bytes` warning 已 detect；workflow agent 失敗 channel 直接 skip 不阻其他 |
| Entity frontmatter parse 失敗 | Sibling 列入「⚠️ Parse 失敗」段、不阻斷其他 entity 掃描 |
| 同 entity 被多 sibling propose 不同 action | **不在 sibling 層 dedup**（各看角度不同）；`/wiki-actions` 合併 action queue 時 group by entity |
| `/wiki-actions` one-liner 跑失敗 | 列「⚠️ Action 失敗」+ stdout/stderr、不寫 frontmatter declined（避免假拒絕）、等 user 重試或手動 |
| Recommendation false positive 多 | 直接 decline 累積、看 trial-review 是否 channel 該拿掉 |
| Sibling 報告 > 500 bytes 但內容垃圾 | Trial-review 時看 adoption 率，低於 baseline 標 channel for removal |

---

## Smoke test 計畫（4 週分階段）

### Week 1：寫完 + 跑通

- Day 1-2：寫 4 個 wrapper（每個 ~80 行 + PROMPT heredoc）+ rename `wiki-candidates-daily.sh` OUT path + 改 `local-analysis.js` CHANNELS
- Day 3：跑 `/daily-local` 看 5 個 wiki-* report 是否正常產出（6 段結構 + recommendation block + > 500 bytes）
- Day 4：digest 是否合成正確（5 wiki 段連續顯示）
- Day 5：跑 `/wiki-actions` 跑通 propose → approve → execute → 寫 frontmatter flow
- Day 6-7：驗 declined / hold / override_until 標記後 sibling 正確跳過

### Week 2：Tune 判準

- 看 4 個 sibling 各自的 noise 水準
- 調 graduation「Changelog 近 1 個月」是否太寬 / stale「過期幾天才 surface」threshold
- 調 wiki-cross-link 的 false positive 防護清單（< 5 字元 + 常用詞）

### Week 3-4：Adoption 觀察

- 紀錄 `/wiki-actions` 採用率：HIGH / MED / LOW 各 priority 接受比例
- Baseline 期望（2026-06-15 修正：user 自報既有 candidates 為 MED adoption pattern → 下調）：
  - HIGH ≥ 60% / MED ≥ 30% / LOW ≥ 10%
- 低於 baseline 的 channel → 標 trial-review candidate for removal
- ⚠️ Prerequisite C 觀察：既有 wiki-candidates 35 天 history 中 **5/22 + 5/23 兩天 5 bytes short-circuit failure（~6% 失敗率）**。加 4 sibling 後期望任一日失敗率最高 ~24%。trial 期間 monitor `< 500 bytes` warning，連續 3 天 short-circuit 同 sibling → 升 root-cause investigation。

### Trial-review 排程

寫進 `~/Desktop/projects/.claude/trials/active.md` 4 個 entry：

```
- 2026-06-15: wiki-lint-daily sibling (4 週 review @ 2026-07-13)
- 2026-06-15: wiki-cross-link-daily sibling (4 週 review @ 2026-07-13)
- 2026-06-15: wiki-graduation-daily sibling (4 週 review @ 2026-07-13)
- 2026-06-15: wiki-stale-daily sibling (4 週 review @ 2026-07-13)
```

4 週後 `~/.claude/hooks/trial-review.sh` 自動 surface review prompt → user 看 adoption 數字決定保留 / 拿掉 / tune。

---

## Open questions / 未來考慮

1. **wiki-audit Phase A+B**（per-page citation verify）是否該補做？
   - kfchou wiki-audit 的設計參考價值高（two-phase + subagent fact-check per source）
   - 但 token cost 重（per page 一個 subagent）→ 先不做、看 trial 完後 wiki 治理品質是否還缺這層
2. **Atom layer 是否該落地？**
   - `_schema.md` 提過「以後若要加 atom layer 必須能追溯」半成品
   - wiki-audit 若做、可順便落 atom layer
3. **同 entity multi-sibling propose dedup 是否該移到 sibling 層？**
   - 目前設計 by `/wiki-actions` 合併時 dedup
   - 如果 4 週後發現 noise 太大可考慮移到 sibling
4. **`/daily-local` 是否該加 SessionStart hook 主動 surface？**
   - 目前 user morning 主動跑 `/daily-local`，被動模式
   - 加 hook 變主動 surface 但可能 over-engineering

---

## 設計參考

- [[memory-consolidation-rejection-landscape]] — 14 次同槽否決紀錄
- [[reference_obsidian_second_brain_eval_2026_06_02]] — 上次 wiki 工具評估
- [[feedback_daily_automation_inventory_first]] — Inventory-first feedback（本設計直接觸發）
- [[reference_mempalace_read_write_imbalance]] — mempalace 退役教訓（curation > retrieval）
- `~/code/social-info/scripts/local-analysis/wiki-candidates-daily.sh` — pattern 模板（6 段 + follow-up + hold/declined 機制）
- `~/.claude/workflows/local-analysis.js` — dispatcher
- `~/.claude/commands/wiki-promote.md` — 並列 user-triggered 寫入端
- kfchou/wiki-skills SKILL.md — wiki-audit Phase A/B 設計參考（未來考慮）
- sametbrr/llm-wiki-manager — lint 結構參考
- Ar9av/obsidian-wiki — cross-linker 設計參考

---

## Out of scope

- Raw doc → wiki ingest pipeline（user 確認無需求）
- Memory ↔ Wiki 同步（user 確認漂移是設計）
- wiki-audit Phase A+B（先列 future consideration）
- 改 `~/.claude/wiki/` 既有 65 entity 結構（不動結構）
- 改 既有 `/wiki-promote` 命令（並列保留）
- 新增 SessionStart hook（user-pulled via `/daily-local` 已夠）
- 新增 cron / launchd（既有 user-pulled 模式不變）
