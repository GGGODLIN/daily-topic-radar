# Wiki 自動化實作 Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 wiki 治理痛點（lint / cross-link / graduation / stale）接到既有 `~/code/social-info/scripts/local-analysis/` sibling 體系，加 4 個 daily sibling shell script + 1 個 `/wiki-actions` slash command 當唯一寫入端，trial-review 4 週後評 adoption。

**Architecture:** Sibling shell + `claude -p` heredoc PROMPT pattern（100% 仿 `wiki-candidates-daily.sh`），透過 `~/.claude/workflows/local-analysis.js` dispatcher 接線。Wiki 治理任務 read-only、寫入透過新 `/wiki-actions` slash command 由 user 拍板執行。

**Tech Stack:** bash 5+, `claude -p` CLI, JavaScript (Workflow tool), markdown (commands/skills/schema)

**Spec reference:** `~/code/social-info/docs/superpowers/specs/2026-06-15-wiki-automation-design.md`

**Affected repos:**
- `~/code/social-info/` — 4 個 sibling + 1 rename
- `~/.claude/` — schema + workflow + slash command
- `~/Desktop/projects/.claude/` — trial-review entries

---

## Task 1: Schema 補充 — `~/.claude/wiki/_schema.md`

**Files:**
- Modify: `~/.claude/wiki/_schema.md`

- [ ] **Step 1: Read 現有 schema**

```bash
cat ~/.claude/wiki/_schema.md
```

- [ ] **Step 2: 加 propose-level 標記段**

在 `~/.claude/wiki/_schema.md` 的「Entity page schema」段後（`### 欄位說明` 表格之前）插入新 section：

```markdown
### Propose-level 標記（user 拍板拒絕後寫進來，sibling 下次掃自動跳過）

```yaml
wiki_lint:
  <finding-key>: declined          # 如 orphan: declined / broken_link_xxx: declined
wiki_cross_link:
  <entity-pair>: declined          # 如 mempalace-mempalace: declined
wiki_graduation:
  hold-<reason>                    # 如 hold-until-cluster-stable
wiki_stale:
  override_until: YYYY-MM-DD       # lifecycle:stale 但這日期前不要 nudge
```

對應 sibling：`wiki-lint-daily.sh` / `wiki-cross-link-daily.sh` / `wiki-graduation-daily.sh` / `wiki-stale-daily.sh`。

跟既有 `wiki_promote: declined/hold-*` 同 mental model。
```

- [ ] **Step 3: Verify schema edit 有效（no broken markdown）**

```bash
head -100 ~/.claude/wiki/_schema.md
```

Expected: 新段在「Entity page schema」段後可見、不破壞 frontmatter 段、不破壞既有 graduation 段。

- [ ] **Step 4: Commit**

`~/.claude/` 是 cross-machine sync repo——確認 commit policy：

```bash
cd ~/.claude && git status
```

如果 user 有自動 sync 流程則照流程；否則手動 commit：

```bash
cd ~/.claude && git add wiki/_schema.md && git commit -m "feat(wiki): add propose-level decline/hold/override frontmatter schema"
```

---

## Task 2: 補 llm-model-landscape.md frontmatter

**Files:**
- Modify: `~/.claude/wiki/llm-model-landscape.md`

**Why this task:** Prerequisite A 發現 65 entity 中 1 個沒 frontmatter（`llm-model-landscape.md`）。Sibling 啟動前補完讓 health rate = 100%。

- [ ] **Step 1: Read 現有 entity 內容**

```bash
head -20 ~/.claude/wiki/llm-model-landscape.md
```

Expected: 直接 `# Header` 開頭、無 `---` frontmatter delimiter。

- [ ] **Step 2: 加 frontmatter**

從 `~/.claude/wiki/index.md` 查 entity 對應的 confidence/lifecycle 資訊（既有 index 有列）：

```bash
grep -A 1 "llm-model-landscape" ~/.claude/wiki/index.md
```

依 index 資訊插入 frontmatter 到 file 開頭（Edit tool）：

```yaml
---
topic: llm-model-landscape
last_updated: 2026-06-14
sources:
  - session-extracted-from-cluster
confidence: high
lifecycle: reviewed
---

# LLM Model Landscape
```

具體 confidence / lifecycle 值依 index.md 對應行決定（high / reviewed 是 prereq 數據顯示既有 entity 普遍狀態，但要 verify）。

- [ ] **Step 3: Verify frontmatter parse**

```bash
python3 -c "
import re
with open('/Users/linhancheng/.claude/wiki/llm-model-landscape.md') as f: c = f.read()
m = re.match(r'^---\n(.*?)\n---', c, re.DOTALL)
print('Frontmatter found:', bool(m))
if m:
    for k in ['topic', 'last_updated', 'confidence', 'lifecycle', 'sources']:
        print(f'  {k}:', bool(re.search(rf'^{k}:', m.group(1), re.MULTILINE)))
"
```

Expected: Frontmatter found: True + 5 個欄位都 True

- [ ] **Step 4: Commit**

```bash
cd ~/.claude && git add wiki/llm-model-landscape.md && git commit -m "fix(wiki): add missing frontmatter to llm-model-landscape entity"
```

---

## Task 3: Rename wiki-candidates 路徑

**Files:**
- Modify: `~/code/social-info/scripts/local-analysis/wiki-candidates-daily.sh:15`
- Modify: `~/.claude/workflows/local-analysis.js:18`

**Why this task:** 統一 5 個 wiki-* report 命名前綴（`$DATE-wiki-{candidates,lint,cross-link,graduation,stale}.md`）。歷史 `$DATE-wiki.md` 留著不動。

- [ ] **Step 1: 改 wrapper OUT path**

`~/code/social-info/scripts/local-analysis/wiki-candidates-daily.sh:15`：

```bash
# 改
OUT="$OUT_DIR/$DATE-wiki.md"
# 為
OUT="$OUT_DIR/$DATE-wiki-candidates.md"
```

LOG path 同步改（line 16）：

```bash
# 改
LOG="$LOG_DIR/local-analysis-wiki-$DATE.log"
# 為
LOG="$LOG_DIR/local-analysis-wiki-candidates-$DATE.log"
```

- [ ] **Step 2: 改 workflow CHANNELS key**

`~/.claude/workflows/local-analysis.js:18`：

```javascript
// 改
{ key: 'wiki', freq: 'daily', kind: 'llm', src: `${W}/wiki-candidates-daily.sh` },
// 為
{ key: 'wiki-candidates', freq: 'daily', kind: 'llm', src: `${W}/wiki-candidates-daily.sh` },
```

- [ ] **Step 3: Smoke test — 獨立跑 wrapper**

```bash
bash ~/code/social-info/scripts/local-analysis/wiki-candidates-daily.sh
ls -la ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-candidates.md
wc -c ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-candidates.md
```

Expected:
- 檔案存在於新命名路徑 `$DATE-wiki-candidates.md`
- 大小 > 500 bytes（仿既有 baseline）

- [ ] **Step 4: Verify 既有歷史 `$DATE-wiki.md` 仍存在（不動）**

```bash
ls ~/code/social-info/reports/local-analysis/2026-05-12-wiki.md
ls ~/code/social-info/reports/local-analysis/2026-06-14-wiki.md
```

Expected: 兩個都還在（不被改名）。

- [ ] **Step 5: Commit**

```bash
cd ~/code/social-info && git add scripts/local-analysis/wiki-candidates-daily.sh && git commit -m "refactor(local-analysis): rename wiki-candidates output to \$DATE-wiki-candidates.md for prefix grouping"
cd ~/.claude && git add workflows/local-analysis.js && git commit -m "refactor(workflows): rename local-analysis channel key 'wiki' to 'wiki-candidates'"
```

---

## Task 4: 寫 `wiki-lint-daily.sh`

**Files:**
- Create: `~/code/social-info/scripts/local-analysis/wiki-lint-daily.sh`

**best-of-N**: invoke Workflow `{name: best-of-n-implement, args: {task: "寫 ~/code/social-info/scripts/local-analysis/wiki-lint-daily.sh，仿 wiki-candidates-daily.sh pattern。PROMPT heredoc 含：(1) 4 判準（orphan / broken [[]] / frontmatter 缺欄位 / 重複 entity）(2) 7 段強制 markdown 結構（## 掃描範圍 / 前一日 follow-up / 🔴 Broken links / 🟡 Orphan pages / 🟢 Frontmatter 缺欄位 / ⚠️ 可能重複 entity / 🎯 今日推薦 actions）(3) Recommendation block 每 finding（在哪 / 為什麼 / 建議 action / One-liner / 預估 cost / Confidence）(4) wiki_lint.<finding-key>: declined frontmatter 標記處理 (5) 前一日 follow-up 機制（carry forward / ✅ 解決標記）(6) < 500 bytes warning。完整 spec 見 ~/code/social-info/docs/superpowers/specs/2026-06-15-wiki-automation-design.md section 2.1 + 3 + 4 + 5。驗收：bash 跑成功 / report 落 $DATE-wiki-lint.md / size > 500 bytes / 7 段標題出現 / 至少含 1 個完整 recommendation block 格式（在 finding 段內）。", n: 3, testCmd: "bash ~/code/social-info/scripts/local-analysis/wiki-lint-daily.sh && OUT=~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-lint.md && [ $(wc -c < \"$OUT\") -gt 500 ] && [ $(grep -c '^## ' \"$OUT\") -ge 7 ]"}}`，勝者 diff 由 main git apply 後重跑 testCmd 驗證。後續 Task 6/8/10 套勝者 pattern 不再 best-of-N。

- [ ] **Step 1: 寫 wrapper（完整 content）**

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
OUT="$OUT_DIR/$DATE-wiki-lint.md"
LOG="$LOG_DIR/local-analysis-wiki-lint-$DATE.log"

cd "$REPO_DIR"

PROMPT=$(cat <<'EOF'
你是 wiki lint audit agent。任務：掃 ~/.claude/wiki/*.md 自身結構找 orphan/broken link/frontmatter 缺欄位/重複 entity，產一份 markdown 報告。

# 第一原則：永遠輸出完整 7 段 markdown

不論 finding 數量、不論判準是否全失敗，**第一個 byte 就是 `## 掃描範圍`**。7 段標題逐字照抄、缺一不可：

```
## 掃描範圍
（時間 / 掃了幾個 entity / 5 個欄位完整率）

## 前一日 follow-up
（昨日 lint 出的 finding 今天還在不在；對每個昨日 finding 標 ✅ 解決 / carry forward）

## 🔴 Broken links (must fix)
（用 `### Broken link: [[xxx]] in <slug>.md` 格式列；無寫「無 broken link」、不省略段標題）

## 🟡 Orphan pages (可能該 cross-link 或刪除)
（entity 沒被任何其他 wiki entity 或 memory cluster 引用；無寫「無 orphan」）

## 🟢 Frontmatter 缺欄位 (補就好)
（缺 topic / last_updated / confidence / lifecycle / sources 任一；無寫「frontmatter 完整」）

## ⚠️ 可能重複 entity (建議合併)
（兩個 entity TL;DR 相似度高，疑似同義內容散兩檔；無寫「無重複」）

## 🎯 今日推薦 actions (按 cost-benefit 排序)
```

**禁止輸出**：
- 單行 `skip` / `無 finding` / 空檔 / 任何 < 500 bytes 報告
- 整份報告用 code fence 包起來
- Preamble（「以下是 ...」「整理完...」）
- 略過任何 1 個段標題

歷史報告 5-10K bytes 是 baseline。今日 < 500 bytes 視為 short-circuit failure。

# Promote-status 標記處理（最優先）

掃到的 entity 若 frontmatter 出現 `wiki_lint:` 段，按其值處理、**不進入判準流程**：

- `wiki_lint.<finding-key>: declined` → 該 finding 對該 entity **完全跳過**，不列任何段
- `wiki_lint.<finding-key>: hold-<reason>` → 列「⏸ HOLD」（額外段在 「🎯 今日推薦 actions」之前）、不催促

# 判準

對每個 entity 評估 4 條：

1. **Orphan**: `grep -r "[[<slug>]]"` in `~/.claude/wiki/` + `~/.claude/memory/` 共 0 命中 → 列入 🟡 Orphan
2. **Broken `[[]]`**: entity 內文有 `[[xxx]]` 但 `~/.claude/wiki/xxx.md` 不存在 → 列入 🔴 Broken
3. **Frontmatter 缺欄位**: 缺 `topic` / `last_updated` / `confidence` / `lifecycle` / `sources` 任一 → 列入 🟢 缺欄位
4. **重複 entity**: 兩個 entity TL;DR 相似度 > 70% → 列入 ⚠️ 重複

# Recommendation block 格式（每個 finding 必帶）

```
### <emoji> <finding type>: <details>
- **在哪**: <file path>:<line>
- **為什麼**: <短解釋>
- **建議 action**（選一）:
  - A. <option 1>
  - B. 標 `wiki_lint.<finding-key>: declined`（拒絕 propose）
- **One-liner**:
  ```bash
  <command>
  ```
- **預估 cost**: <時間>
- **Confidence**: high / medium / low
```

# 「🎯 今日推薦 actions」段格式

按 cost-benefit 排序的扁平 list（**僅列有 one-liner 的高 confidence finding**）：

```
1. **[HIGH] <action>** (~<cost>, conf:<level>)
   → `<one-liner command>`

2. **[MED] <action>** (...)
   ...
```

Priority 規則：
- HIGH = Broken link / Frontmatter 缺 required 欄位（會 break sibling）
- MED = Orphan（need cross-link decision）
- LOW = 重複 entity 提案合併

# 前一日 follow-up（必做）

讀 `/Users/linhancheng/code/social-info/reports/local-analysis/`，找昨天 (today - 1d) 的 `wiki-lint.md`：

- 抽出昨日 4 個 finding 段的 entity + finding key
- 對每個查今日 wiki 狀態：
  - 問題消失 → 標「✅ 解決」、不再列今日
  - 問題還在 → carry forward 進今日對應段
- 昨日無 report → 寫「無前一日報告」

# 紀律

- 嚴格 read-only：絕對不修 wiki entity、不寫任何 memory
- 只產 markdown 到 stdout
- 第一個 byte 永遠是 `## 掃描範圍`、7 段標題不省略
EOF
)

{
  echo "=== wiki-lint started: $(date) ==="
  "$CLAUDE" -p "$PROMPT" > "$OUT" 2>&1
  size=$(wc -c < "$OUT")
  echo "=== wiki-lint finished: $(date) ==="
  echo "Output: $OUT ($size bytes)"
  if [ "$size" -lt 500 ]; then
    echo "⚠️  WARNING: output < 500 bytes ($size), agent likely short-circuited"
    echo "--- raw output start ---"
    cat "$OUT"
    echo "--- raw output end ---"
  fi
} >> "$LOG" 2>&1
```

- [ ] **Step 2: chmod +x**

```bash
chmod +x ~/code/social-info/scripts/local-analysis/wiki-lint-daily.sh
```

- [ ] **Step 3: Smoke test 獨立跑**

```bash
bash ~/code/social-info/scripts/local-analysis/wiki-lint-daily.sh
ls -la ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-lint.md
wc -c ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-lint.md
```

Expected:
- File 落檔於 `$DATE-wiki-lint.md`
- 大小 > 500 bytes（仿 wiki-candidates baseline）
- Log 在 `~/code/social-info/logs/local-analysis-wiki-lint-$DATE.log`

- [ ] **Step 4: Verify output 7 段結構**

```bash
grep "^## " ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-lint.md
```

Expected: 7 段標題依序出現：
```
## 掃描範圍
## 前一日 follow-up
## 🔴 Broken links (must fix)
## 🟡 Orphan pages (可能該 cross-link 或刪除)
## 🟢 Frontmatter 缺欄位 (補就好)
## ⚠️ 可能重複 entity (建議合併)
## 🎯 今日推薦 actions (按 cost-benefit 排序)
```

- [ ] **Step 5: Verify recommendation block 出現**

```bash
grep "^- \*\*One-liner\*\*" ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-lint.md | head -3
```

Expected: 至少 1 行「**One-liner**」（若今日真有 finding）；若 0 行（所有段都「無 finding」）→ 是正常狀態、但要 verify 7 段標題 ok。

- [ ] **Step 6: Commit**

```bash
cd ~/code/social-info && git add scripts/local-analysis/wiki-lint-daily.sh && git commit -m "feat(wiki-audit): add wiki-lint daily sibling for orphan/broken/frontmatter/duplicate detection"
```

---

## Task 5: 接 wiki-lint 進 workflow + smoke test

**Files:**
- Modify: `~/.claude/workflows/local-analysis.js`

- [ ] **Step 1: 加 CHANNELS entry**

`~/.claude/workflows/local-analysis.js` 在 `wiki-candidates` channel 之後加：

```javascript
{ key: 'wiki-lint', freq: 'daily', kind: 'llm', src: `${W}/wiki-lint-daily.sh` },
```

完整 array 樣貌（Task 3 + 本 Task 後）：

```javascript
const CHANNELS = [
  { key: 'memory', freq: 'daily', kind: 'llm', src: '/Users/linhancheng/.claude/commands/memory-audit.md' },
  { key: 'wiki-candidates', freq: 'daily', kind: 'llm', src: `${W}/wiki-candidates-daily.sh` },
  { key: 'wiki-lint', freq: 'daily', kind: 'llm', src: `${W}/wiki-lint-daily.sh` },  // 新
  { key: 'recap', freq: 'daily', kind: 'llm', src: `${W}/recap-daily.sh` },
  // ...其他既有 channel 不動...
]
```

- [ ] **Step 2: 跑 `/daily-local` 驗證 wiki-lint 段出現在 digest**

開 CC session 跑 `/daily-local`：

```
> /daily-local
```

等 workflow 跑完看 digest output。

Expected:
- Digest 含 `## wiki-lint` section
- Summary 是 3-5 行繁中濃縮（非 raw full report）
- 沒 error / channel skip

- [ ] **Step 3: Verify report file 也由 workflow 產出**

```bash
ls -la ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-lint.md
```

Expected: file mtime 是 workflow 跑的時間（不是 Task 4 step 3 獨立跑的時間）。

- [ ] **Step 4: Commit**

```bash
cd ~/.claude && git add workflows/local-analysis.js && git commit -m "feat(workflows): add wiki-lint channel to local-analysis dispatcher"
```

---

## Task 6: 寫 `wiki-cross-link-daily.sh`

**Files:**
- Create: `~/code/social-info/scripts/local-analysis/wiki-cross-link-daily.sh`

- [ ] **Step 1: 寫 wrapper（完整 content）**

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
OUT="$OUT_DIR/$DATE-wiki-cross-link.md"
LOG="$LOG_DIR/local-analysis-wiki-cross-link-$DATE.log"

cd "$REPO_DIR"

PROMPT=$(cat <<'EOF'
你是 wiki cross-link audit agent。任務：掃 ~/.claude/wiki/*.md 跟 ~/.claude/memory/**/*.md 找平文 mention 沒包 `[[]]`、backlink 漂移、新 entity 待 cross-link，產 markdown 報告。

# 第一原則：永遠輸出完整 7 段 markdown

```
## 掃描範圍
（時間 / 掃了幾個 entity / 共多少 `[[]]` references）

## 前一日 follow-up

## 🔗 平文 mention 該補 wikilink
（用 `### <entity name> mentioned in <file>:<line>` 格式列）

## ↔️ Backlink 漂移
（A 引用 B 但 B 沒回鏈 A）

## 🆕 新 entity 待 cross-link
（近 7 天加但 0 其他 entity 引用）

## 已掃但結構正常
（snapshot 數字）

## 🎯 今日推薦 actions
```

`< 500 bytes` 視為 short-circuit。

# Promote-status 標記處理

`wiki_cross_link.<entity-pair>: declined` → 該 pair 不再 propose（entity-pair 格式 `<slug-A>-<slug-B>` alphabetically sorted）

# 判準

1. **平文 mention 沒 wikilink**: wiki entity A 內文出現 "mempalace"（純字、不含 `[[]]`）但既有 `~/.claude/wiki/mempalace.md` 存在 → 該補 `[[mempalace]]`
2. **記憶 cluster 引用漂移**: memory cluster 用 `[[name]]` 引用某 wiki entity，但 entity 沒 backlink 回 cluster source
3. **新 entity 加入後沒 backlink**: `last_updated:` 在 7 天內加的 entity，其他既有 entity 0 引用

# False positive 防護（必做）

排除以下 case 不 propose：

1. Entity slug 短於 5 字元（避免單字 match）
2. Slug 屬於常用詞清單（不 propose）：
   - `wiki` / `memory` / `hook` / `skill` / `agent` / `tool` / `config`
   - `api` / `cli` / `claude` / `plan` / `spec` / `task`
   - `index` / `log` / `report` / `digest` / `source`
3. Mention 出現在 markdown code block / inline code `` ` ` `` 內（不 propose）
4. Entity A 引用 entity A 自己（self-reference 不 propose backlink 漂移）

# Recommendation block 格式

每個 finding 必帶：
- **在哪**: <file path>:<line>
- **為什麼**: <短解釋>
- **建議 action**（選一）:
  - A. 補 `[[<slug>]]` wikilink
  - B. 標 `wiki_cross_link.<pair>: declined`（plain mention 真的指普通詞）
- **One-liner**:
  ```
  sed -i '' 's/<mention>/[[<slug>]]/g' <file>
  ```
- **預估 cost**: 30-60 秒 / finding
- **Confidence**: high (typo-free wikilink) / medium (need context check)

# 「🎯 今日推薦 actions」

Priority:
- HIGH = backlink 漂移（cluster→wiki 但 wiki 無回鏈、explicit broken）
- MED = 平文 mention 補 wikilink（high confidence case）
- LOW = 新 entity 待 cross-link（建議 review）

# 前一日 follow-up

抽昨日 finding entity + pair → 看今日對應 wiki entity 是否已加 `[[]]` → 標 ✅ 解決 / carry forward

# 紀律

- 嚴格 read-only
- 第一個 byte 必是 `## 掃描範圍`
- 7 段不省略
EOF
)

{
  echo "=== wiki-cross-link started: $(date) ==="
  "$CLAUDE" -p "$PROMPT" > "$OUT" 2>&1
  size=$(wc -c < "$OUT")
  echo "=== wiki-cross-link finished: $(date) ==="
  echo "Output: $OUT ($size bytes)"
  if [ "$size" -lt 500 ]; then
    echo "⚠️  WARNING: output < 500 bytes ($size), agent likely short-circuited"
    cat "$OUT"
  fi
} >> "$LOG" 2>&1
```

- [ ] **Step 2: chmod +x**

```bash
chmod +x ~/code/social-info/scripts/local-analysis/wiki-cross-link-daily.sh
```

- [ ] **Step 3: Smoke test 獨立跑**

```bash
bash ~/code/social-info/scripts/local-analysis/wiki-cross-link-daily.sh
ls -la ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-cross-link.md
wc -c ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-cross-link.md
```

Expected: file 存在 + > 500 bytes

- [ ] **Step 4: Verify 7 段結構**

```bash
grep "^## " ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-cross-link.md
```

Expected: 7 段標題依序出現

- [ ] **Step 5: 抽樣驗 false positive 防護**

```bash
# 看報告有沒有列「wiki」「memory」「hook」這類常用詞為 cross-link candidate
grep -E "\[\[(wiki|memory|hook|skill|agent|tool)\]\]" ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-cross-link.md
```

Expected: 0 命中（常用詞排除清單生效）。若有命中 → tune PROMPT 防護段。

- [ ] **Step 6: Commit**

```bash
cd ~/code/social-info && git add scripts/local-analysis/wiki-cross-link-daily.sh && git commit -m "feat(wiki-audit): add wiki-cross-link daily sibling with false-positive guard"
```

---

## Task 7: 接 wiki-cross-link 進 workflow + smoke test

**Files:**
- Modify: `~/.claude/workflows/local-analysis.js`

- [ ] **Step 1: 加 CHANNELS entry**

```javascript
{ key: 'wiki-cross-link', freq: 'daily', kind: 'llm', src: `${W}/wiki-cross-link-daily.sh` },
```

放在 `wiki-lint` channel 之後（filename 字典序）。

- [ ] **Step 2: 跑 `/daily-local`**

```
> /daily-local
```

Expected: digest 含 `## wiki-cross-link` section。

- [ ] **Step 3: Verify report 由 workflow 產出**

```bash
ls -la ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-cross-link.md
```

- [ ] **Step 4: Commit**

```bash
cd ~/.claude && git add workflows/local-analysis.js && git commit -m "feat(workflows): add wiki-cross-link channel"
```

---

## Task 8: 寫 `wiki-graduation-daily.sh`

**Files:**
- Create: `~/code/social-info/scripts/local-analysis/wiki-graduation-daily.sh`

- [ ] **Step 1: 寫 wrapper（完整 content）**

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
OUT="$OUT_DIR/$DATE-wiki-graduation.md"
LOG="$LOG_DIR/local-analysis-wiki-graduation-$DATE.log"

cd "$REPO_DIR"

PROMPT=$(cat <<'EOF'
你是 wiki graduation audit agent。任務：掃 ~/.claude/wiki/*.md 找達成 4 條判準的 entity 提案升 `~/.claude/CLAUDE.md` rule 或 `~/.claude/rules/` 條目。

# 第一原則：永遠輸出完整 7 段 markdown

```
## 掃描範圍
（時間 / 掃了幾個 entity / 各 confidence/lifecycle 統計）

## 前一日 follow-up

## 🎓 升級候選 (4 條全滿足)
（用 `### 候選 N: <slug>` 格式列）

## ⏸ 接近成熟 (滿足 3/4 條)
（差 1 條的列出來、寫差哪條、預估再 N 天可重評）

## 已升級紀錄 (snapshot)
（從 `_schema.md` 反向降級段 + `~/.claude/CLAUDE.md` / `~/.claude/rules/` 對位反查）

## 已掃但不夠成熟

## 🎯 今日推薦 actions
```

`< 500 bytes` 視為 short-circuit。

# Promote-status 標記處理

`wiki_graduation: hold-<reason>` → 列「⏸ HOLD」+ 一行 hold 理由，不催促、不計久懸天數。

# 判準（4 條全滿足才列「🎓 升級候選」）

1. `confidence: high`
2. `lifecycle: verified`
3. Changelog 近 1 個月無大改動：最後 changelog entry 日期 > 30 天前
4. 跨 cluster / standalone 引用 ≥ 3：`grep -r "\[\[<slug>\]\]" ~/.claude/memory/` 統計

滿足 3/4 條的 → 列「⏸ 接近成熟」段。

# 升級 target 判定規則

對每個升級候選 entity，判定升 `CLAUDE.md` 還是 `~/.claude/rules/`：

- **升 `~/.claude/CLAUDE.md`**:
  - Entity 內容是 cross-cutting / always-on 級規則（每次 Claude session 都該知道）
  - 例：紀律性 rule、code style、commit policy
- **升 `~/.claude/rules/`**:
  - Entity 內容是特定 domain（performance / codex-rescue / dispatch 等）trigger-based 規則
  - 例：domain-specific best practice、scope-limited convention
- **不適合升**:
  - Entity 內容是 entity-centric snapshot（landscape / toolkit / pattern）不是 rule
  - 維持 wiki entity 即可、不該升

# Recommendation block 格式

```
### 候選 N: <slug>
- **4 條判準逐條 verify**:
  - ✅ confidence: high
  - ✅ lifecycle: verified
  - ✅ Changelog 最後 entry: <YYYY-MM-DD> (X 天前)
  - ✅ 跨 cluster 引用: <count> 次（list 引用源）
- **升級 target**: CLAUDE.md / ~/.claude/rules/<name>.md / 不適合升（純 snapshot）
- **預估會吸收哪段 rule**: <1-2 句描述要從 entity 提煉出的 rule 文字>
- **cross-project 適用性**: high / medium / low
- **建議 action**:
  - A. 升 CLAUDE.md / rules/（給具體 target file path）
  - B. 標 `wiki_graduation: hold-<reason>`（暫不升）
- **One-liner**: 無（升級需手動寫 rule 文字、不 one-liner-able）
- **預估 cost**: 30-60 分鐘（自己寫 rule + verify cross-project 適用）
- **Confidence**: high (clear-cut 4/4) / medium (需 user 判 cross-project)
```

# 「🎯 今日推薦 actions」

Priority:
- HIGH = 4/4 滿足且 confidence=high
- MED = 4/4 滿足但 confidence=medium
- LOW = 3/4（接近但要等 1 條）

# 前一日 follow-up

抽昨日「升級候選」+「接近成熟」段 entity → 看今日狀態：
- 已升 CLAUDE.md / rules/ → 標 ✅ 升級完成
- 仍候選 → carry forward
- frontmatter 加 `wiki_graduation: hold-*` → 移到「⏸ HOLD」

# 紀律

- 嚴格 read-only
- 第一個 byte 必是 `## 掃描範圍`
- 7 段不省略
EOF
)

{
  echo "=== wiki-graduation started: $(date) ==="
  "$CLAUDE" -p "$PROMPT" > "$OUT" 2>&1
  size=$(wc -c < "$OUT")
  echo "=== wiki-graduation finished: $(date) ==="
  echo "Output: $OUT ($size bytes)"
  if [ "$size" -lt 500 ]; then
    echo "⚠️  WARNING: output < 500 bytes ($size), agent likely short-circuited"
    cat "$OUT"
  fi
} >> "$LOG" 2>&1
```

- [ ] **Step 2: chmod +x**

```bash
chmod +x ~/code/social-info/scripts/local-analysis/wiki-graduation-daily.sh
```

- [ ] **Step 3: Smoke test 獨立跑**

```bash
bash ~/code/social-info/scripts/local-analysis/wiki-graduation-daily.sh
ls -la ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-graduation.md
wc -c ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-graduation.md
```

Expected: file 存在 + > 500 bytes

- [ ] **Step 4: Verify 7 段結構 + 升級 target 判定出現**

```bash
grep "^## " ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-graduation.md
grep "升級 target" ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-graduation.md | head -3
```

Expected:
- 7 段標題依序出現
- 至少 1 行「升級 target」（若今日有候選）

- [ ] **Step 5: Commit**

```bash
cd ~/code/social-info && git add scripts/local-analysis/wiki-graduation-daily.sh && git commit -m "feat(wiki-audit): add wiki-graduation daily sibling with CLAUDE.md/rules/ target judgment"
```

---

## Task 9: 接 wiki-graduation 進 workflow + smoke test

**Files:**
- Modify: `~/.claude/workflows/local-analysis.js`

- [ ] **Step 1: 加 CHANNELS entry**

```javascript
{ key: 'wiki-graduation', freq: 'daily', kind: 'llm', src: `${W}/wiki-graduation-daily.sh` },
```

放在 `wiki-cross-link` 之後（字典序）。

- [ ] **Step 2: 跑 `/daily-local`**

Expected: digest 含 `## wiki-graduation` section。

- [ ] **Step 3: Commit**

```bash
cd ~/.claude && git add workflows/local-analysis.js && git commit -m "feat(workflows): add wiki-graduation channel"
```

---

## Task 10: 寫 `wiki-stale-daily.sh`

**Files:**
- Create: `~/code/social-info/scripts/local-analysis/wiki-stale-daily.sh`

- [ ] **Step 1: 寫 wrapper（完整 content）**

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
OUT="$OUT_DIR/$DATE-wiki-stale.md"
LOG="$LOG_DIR/local-analysis-wiki-stale-$DATE.log"

cd "$REPO_DIR"

PROMPT=$(cat <<'EOF'
你是 wiki stale audit agent。任務：掃 ~/.claude/wiki/*.md 找 lifecycle:stale 久未 refresh 跟久未 changelog 但非 verified 的 entity，產 markdown 報告。

注意：本 sibling **不用 `stale-by:` 欄位**（2026-06-15 prerequisite A audit 顯示 65 entity 全部 0 用該欄位）。判準改靠 `lifecycle: stale` + Changelog 時間軸。

# 第一原則：永遠輸出完整 7 段 markdown

```
## 掃描範圍
（時間 / 掃了幾個 entity / lifecycle 分布統計）

## 前一日 follow-up

## 💤 Lifecycle:stale 久未 refresh (建議 archive 或 promote 進 CLAUDE.md 後刪除)
（用 `### <slug>: stale 已 N 天` 格式列）

## 📜 久未 changelog 但非 verified (建議升 lifecycle 或補 changelog)

## ⚠️ Parse 失敗 (frontmatter 解析錯)
（列無法 parse frontmatter 的 entity；無寫「無」）

## 已掃但 fresh
（snapshot 數字）

## 🎯 今日推薦 actions
```

`< 500 bytes` 視為 short-circuit。

# Promote-status 標記處理

`wiki_stale.override_until: YYYY-MM-DD` → 該 entity 在此日期前不 nudge、列在「⏸ HOLD」（額外段在「🎯 今日推薦 actions」前），標明 override 日期

# 判準

1. **`lifecycle: stale` 已 N 天**: lifecycle 標為 stale，距 `last_updated:` > 30 天沒 refresh → 列「💤 Lifecycle:stale」
2. **久未 changelog**: confidence 不是 stale、`lifecycle != verified`、最後 changelog entry > 90 天 → 列「📜 久未 changelog」

注意：兩條判準互斥（lifecycle 是 stale 走 1、非 stale 走 2）。

# Recommendation block 格式

#### Lifecycle:stale 案例

```
### <slug>: stale 已 N 天
- **lifecycle**: stale
- **last_updated**: <YYYY-MM-DD> (N 天前)
- **stale 原因**: <從 Changelog 抓最近一次 lifecycle 改 stale 的理由>
- **建議 action**（選一）:
  - A. **Archive**: entity 內容不再有效、直接 git rm
  - B. **Promote 進 CLAUDE.md / rules/**: 重要結論該成 rule、不需 wiki entity
  - C. **Refresh**: 重驗、改 lifecycle: reviewed/verified + 更新 last_updated
  - D. 標 `wiki_stale.override_until: <date>`（暫不處理）
- **One-liner** (case A archive):
  ```bash
  git -C ~/.claude rm wiki/<slug>.md && git commit -m "chore(wiki): archive stale <slug>"
  ```
- **預估 cost**: A: 30 秒 / B: 30 分 / C: 15-60 分
- **Confidence**: medium (取捨需 user 判)
```

#### 久未 changelog 案例

```
### <slug>: changelog 已 N 天無更新
- **lifecycle**: <draft/reviewed>
- **last changelog**: <YYYY-MM-DD> (N 天前)
- **建議 action**（選一）:
  - A. 升 lifecycle: verified（內容已穩定、補 verified 標記）
  - B. 補 changelog entry（描述近 N 天的變化、即使是「無變化、re-verified」）
  - C. 標 `wiki_stale.override_until: <date>`（暫不處理）
- **One-liner**: 無（lifecycle 升級需手動編輯 frontmatter）
- **預估 cost**: 5-10 分
- **Confidence**: high (建議升 verified) / low (changelog 真的有變化要補)
```

# 「🎯 今日推薦 actions」

Priority:
- HIGH = lifecycle:stale 已 60+ 天（建議 archive 或 promote 已逾期）
- MED = lifecycle:stale 30-60 天 + 久未 changelog 180+ 天
- LOW = 久未 changelog 90-180 天

# 前一日 follow-up

抽昨日「💤 stale」+「📜 久未 changelog」段 entity → 看今日狀態：
- 已 archive (entity 不存在) → 標 ✅ 解決
- lifecycle 升 verified → 標 ✅ refreshed
- 加 `wiki_stale.override_until` → 移「⏸ HOLD」段

# 紀律

- 嚴格 read-only
- 第一個 byte 必是 `## 掃描範圍`
- 7 段不省略
EOF
)

{
  echo "=== wiki-stale started: $(date) ==="
  "$CLAUDE" -p "$PROMPT" > "$OUT" 2>&1
  size=$(wc -c < "$OUT")
  echo "=== wiki-stale finished: $(date) ==="
  echo "Output: $OUT ($size bytes)"
  if [ "$size" -lt 500 ]; then
    echo "⚠️  WARNING: output < 500 bytes ($size), agent likely short-circuited"
    cat "$OUT"
  fi
} >> "$LOG" 2>&1
```

- [ ] **Step 2: chmod +x**

```bash
chmod +x ~/code/social-info/scripts/local-analysis/wiki-stale-daily.sh
```

- [ ] **Step 3: Smoke test 獨立跑**

```bash
bash ~/code/social-info/scripts/local-analysis/wiki-stale-daily.sh
ls -la ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-stale.md
wc -c ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-stale.md
```

Expected: file 存在 + > 500 bytes

- [ ] **Step 4: Verify 7 段結構**

```bash
grep "^## " ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-stale.md
```

Expected: 7 段依序出現。

- [ ] **Step 5: Verify mempalace 命中 lifecycle:stale**

mempalace 的 wiki entity (`~/.claude/wiki/mempalace.md`) 是 `lifecycle: stale`（從 prerequisite A 知道）—— 應該被本 sibling 偵測：

```bash
grep "mempalace" ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-stale.md | head -3
```

Expected: 至少 1 行包含 mempalace（最近 last_updated 是 2026-05-07 已超 30 天）。

- [ ] **Step 6: Commit**

```bash
cd ~/code/social-info && git add scripts/local-analysis/wiki-stale-daily.sh && git commit -m "feat(wiki-audit): add wiki-stale daily sibling (lifecycle/changelog-based, no stale-by dependency)"
```

---

## Task 11: 接 wiki-stale 進 workflow + smoke test

**Files:**
- Modify: `~/.claude/workflows/local-analysis.js`

- [ ] **Step 1: 加 CHANNELS entry**

```javascript
{ key: 'wiki-stale', freq: 'daily', kind: 'llm', src: `${W}/wiki-stale-daily.sh` },
```

放在 `wiki-graduation` 之後（字典序）。

完整 5 個 wiki-* CHANNELS（Task 3/5/7/9/11 後）：

```javascript
{ key: 'wiki-candidates', freq: 'daily', kind: 'llm', src: `${W}/wiki-candidates-daily.sh` },
{ key: 'wiki-cross-link', freq: 'daily', kind: 'llm', src: `${W}/wiki-cross-link-daily.sh` },
{ key: 'wiki-graduation', freq: 'daily', kind: 'llm', src: `${W}/wiki-graduation-daily.sh` },
{ key: 'wiki-lint', freq: 'daily', kind: 'llm', src: `${W}/wiki-lint-daily.sh` },
{ key: 'wiki-stale', freq: 'daily', kind: 'llm', src: `${W}/wiki-stale-daily.sh` },
```

- [ ] **Step 2: 跑 `/daily-local`**

Expected: digest 含 5 個 wiki-* section 連續顯示（按字典序）。

- [ ] **Step 3: Verify 5 個 report 都產出**

```bash
ls ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-*.md
```

Expected: 5 個 file 都在（candidates / cross-link / graduation / lint / stale）。

- [ ] **Step 4: Commit**

```bash
cd ~/.claude && git add workflows/local-analysis.js && git commit -m "feat(workflows): add wiki-stale channel (5th wiki-* sibling complete)"
```

---

## Task 12: 寫 `/wiki-actions` slash command

**Files:**
- Create: `~/.claude/commands/wiki-actions.md`

- [ ] **Step 1: 寫 slash command（完整 content）**

```markdown
---
description: 讀今日 wiki-* 5 個 report、合併 action queue、user 拍板後執行 one-liner / 寫 frontmatter
---

# Wiki Actions

讀今日 5 個 wiki-* report、合併「🎯 今日推薦 actions」段、按 priority + entity group 排序、user 拍板後執行批准 action 並寫 frontmatter 標記。

**唯一寫入端**（4 sibling + wiki-candidates 全 read-only）。永遠 user-triggered。

## 1. 讀今日 5 個 report

```bash
DATE=$(date +%Y-%m-%d)
for aspect in candidates lint cross-link graduation stale; do
  echo "=== $aspect ==="
  cat ~/code/social-info/reports/local-analysis/$DATE-wiki-$aspect.md 2>/dev/null || echo "(report missing)"
done
```

如果某個 report missing → 跟 user 確認是 sibling 短路 (< 500 bytes) 還是當日跑失敗、要不要重跑該 sibling。

## 2. 抽各 report 「🎯 今日推薦 actions」段

對每個 report 抽出 `## 🎯 今日推薦 actions` 之後到 next `##` 之前的內容。

Parse 成 actions list，每個 action 含：
- `sibling` (candidates / lint / cross-link / graduation / stale)
- `priority` (HIGH / MED / LOW)
- `entity` (slug，從 `[[<slug>]]` 或 `<slug>:` 抽出，**normalize 去 `[[]]` 後純 slug**)
- `action_desc` (action 文字描述)
- `one_liner` (`→ \`...\`` 後的 command；無 one-liner 標 `(無 one-liner)`)
- `cost` (`~30 秒` / `~15 分鐘` 等)
- `confidence` (high / medium / low)

## 3. 合併演算法（明確規格）

### 3.1 Group by entity

- Group key: entity slug（normalize 後）
- 若 entity 出現在 ≥ 2 個 sibling 的 actions：合併成 entity-level entry
  顯示時列「⚠️ <entity> 被 N 個 sibling 同時 surface」+ 列各 individual action

### 3.2 Priority 排序

- Entity-level priority = MAX(各 sibling 對該 entity 的 priority)
  即：lint 標 HIGH + stale 標 LOW → entity-level 為 HIGH
  ⚠️ **告知 user 這個 MAX 規則可能誤導**（lint HIGH 跟 stale LOW 性質不同但合併成 HIGH）
- 同 priority 內按 confidence 排（high > medium > low）
- 同 confidence 內按 cost 升序（30 秒先於 15 分鐘）

### 3.3 輸出格式給 user

按 entity-level priority 順序列、每 entity 展開列各 sibling 的 individual action：

```
═══ 合併 Action Queue (今日 N 項) ═══

[1] [HIGH ⚠️ MAX-elevated from lint] [[entity-X]]
    被 2 個 sibling surface (lint + stale)：
    1a. [HIGH] lint: 修 broken link [[xxx]] → [[yyy]] (~30s, conf:high)
        → sed -i '' '...' ~/.claude/wiki/entity-X.md
    1b. [LOW] stale: lifecycle:stale 已 45 天 (~15m, conf:medium)
        → 重驗 confidence 或 archive

[2] [HIGH] [[entity-Y]]
    被 1 個 sibling surface (cross-link)
    2a. [HIGH] cross-link: 補 [[xxx]] mention in entity-Y.md:23 (~30s, conf:high)
        → sed -i '' '...'

[3] [MED] ...

[4] [LOW] ...
```

## 4. 等 user 拍板

報告完 surface 後等 user 自然語言回應，按以下 pattern parse（**彈性匹配**、不嚴格限定 5 種）：

- **Approve all**: 「全部做」/「all」/「都做」/「approve all」
- **Approve specific entity**: 「做 entity-X」/「entity-X, entity-Y」/「除了 X 都做」
- **Approve specific number**: 「做 1, 3, 5」/「1-3」/「1 到 5」
- **Decline specific**: 「跳過 X」/「不要 X」/「decline X」
- **Hold specific**: 「hold X 到 2026-09-01」/「X 月底再說」
- **Mixed**: 「做 1, 2, 跳過 3, hold 4」
- **All hold**: 「全部 hold」/「下次再說」/「明天再決定」
- **All skip**: 「跳過」/「都不做」/「skip」

如果回應**模糊**（如「重要的做」/「能做的做」）→ **不要猜、跟 user 確認哪個算重要、哪個算能做**。

## 5. 執行批准的 action

### 5.1 One-liner 安全 guards（必做）

對每個批准的 one-liner，跑前過 4 道 guard：

**Guard 1 — Destructive pattern 黑名單**：grep one-liner 字串、命中以下任一 → 標「⚠️ DESTRUCTIVE: 拒跑、要 user 手動」：

```
rm -rf
rm -r
rm \(no -i flag\)
find .* -delete
xargs rm
> \(no append &>>\)
truncate
dd of=
chmod 7[0-9][0-9]
chmod -R
git reset --hard
git push --force
git push -f
git clean -fd
```

**Guard 2 — Path scope check**：one-liner 操作的檔案路徑必須 fall in：
- `~/.claude/wiki/*.md`
- `~/code/social-info/scripts/local-analysis/*.sh`
- `~/code/social-info/reports/local-analysis/*.md`

超出範圍 → 拒跑 + 列「⚠️ Path 超出 scope, 拒跑」

**Guard 3 — Dry-run 預設**（核心 guard）：對 sed/awk 類 in-place 改動，先 dry-run print diff 給 user 確認才 apply：

```bash
# Dry-run: print 改之後的 content（不寫檔）
sed 's/...//' <file>     # 不加 -i
# 用 git diff 比（wiki entity 是 git tracked）
sed -i.wikiactions.bak 's/...//' <file>
diff <file>.wikiactions.bak <file>
```

User 看 diff 後決定：confirm → 刪 `.wikiactions.bak`；reject → `mv <file>.wikiactions.bak <file>`（restore）。

**Guard 4 — Backup（補保險）**：執行寫入前 `cp <file> <file>.wikiactions.bak.<timestamp>`，跑完 user 確認沒問題後 `rm` backup。

**Cleanup 機制**：本 session 結束時主動跑：

```bash
find ~/.claude/wiki/ -name "*.wikiactions.bak.*" -mtime +1 -delete
```

清理 1 天前的 backup（避免堆積）。

### 5.2 執行流程

對每個批准的 action：

1. 過 4 guards
2. Guard 任一失敗 → 標「⚠️ Guard X 觸發、改成手動指引」+ 給 user 文字步驟
3. 全過 → 跑 dry-run、show diff、user 確認 → 跑 in-place + backup
4. 跑後 verify：再 cat 目標 file 確認改動生效
5. 跑成功 → 刪 backup；失敗 → restore backup + 列 error

對沒 one-liner 的 action → 列 user 手動步驟、不嘗試自動執行。

## 6. 寫 decline / hold / override frontmatter

對 user 拍板「跳過」「hold」的 action，寫進對應 wiki entity frontmatter：

```yaml
# decline 例：
wiki_lint:
  orphan: declined
  broken_link_xxx: declined

# hold 例：
wiki_graduation:
  hold-until-cluster-stable

# override 例：
wiki_stale:
  override_until: 2026-09-01
```

寫入前 show diff（git diff）、user 確認才 commit。

## 7. 完成後 summary

```
═══ /wiki-actions Summary ═══
✅ 執行: X 個 (entity list)
⏸ Hold: Y 個 (entity + override 日期)
🚫 Decline: Z 個 (entity + finding key)
⚠️ Manual 待 user: W 個 (列出哪些需要手動)
🧹 Cleanup: 刪 N 個 backup file
```

## 紀律

- **永遠 user-triggered**（不放 hook、不放 cron）
- **dry-run 預設**（Guard 3 是核心紅線）
- **寫入前 show diff**（不論 wiki entity 或 frontmatter）
- 4 sibling + wiki-candidates 全 read-only，本 command 是唯一寫入端
```

- [ ] **Step 2: Verify slash command load**

```bash
ls -la ~/.claude/commands/wiki-actions.md
```

Expected: file 存在。

- [ ] **Step 3: Smoke test — dry-run mode 跑**

開 CC session 跑：

```
> /wiki-actions
```

期望流程：
1. CC 讀 5 個 report
2. 合併 action queue（按 entity group + priority）
3. Surface 給 user 看
4. 模擬 user 回應「全部 hold 到明天」測試 hold 機制
5. 看 frontmatter 是否正確寫入 `wiki_*.override_until` / `hold-*`

Expected:
- 合併演算法正確（同 entity 多 sibling 合併）
- Guard 觸發 case（destructive command 命中）正確拒跑
- Frontmatter 寫入前 show diff

- [ ] **Step 4: Verify backup cleanup**

```bash
find ~/.claude/wiki/ -name "*.wikiactions.bak.*"
```

Expected: 0 個（session end cleanup 跑掉了）。

- [ ] **Step 5: Commit**

```bash
cd ~/.claude && git add commands/wiki-actions.md && git commit -m "feat(commands): add /wiki-actions for wiki audit action queue (sole writer endpoint with 4 guards)"
```

---

## Task 13: Trial-review entries

**Files:**
- Modify: `~/Desktop/projects/.claude/trials/active.md`

- [ ] **Step 1: Verify trials/active.md 存在**

```bash
ls -la ~/Desktop/projects/.claude/trials/active.md
cat ~/Desktop/projects/.claude/trials/active.md
```

如果不存在 → 建檔：

```bash
mkdir -p ~/Desktop/projects/.claude/trials
touch ~/Desktop/projects/.claude/trials/active.md
```

- [ ] **Step 2: 加 4 個 trial entry**

Append 到 `~/Desktop/projects/.claude/trials/active.md`：

```
- 2026-06-15: wiki-lint-daily sibling (4 週 review @ 2026-07-13) - 看 noise/adoption/false-positive 比率
- 2026-06-15: wiki-cross-link-daily sibling (4 週 review @ 2026-07-13) - 看常用詞防護是否夠/false-positive 比率
- 2026-06-15: wiki-graduation-daily sibling (4 週 review @ 2026-07-13) - 看升 CLAUDE.md vs rules/ 判定一致性
- 2026-06-15: wiki-stale-daily sibling (4 週 review @ 2026-07-13) - 看 archive/promote/refresh 決策分布
```

- [ ] **Step 3: Verify trial-review hook 讀到**

```bash
bash ~/.claude/hooks/trial-review.sh
```

Expected: hook 列出 4 個 entry（即使 review date 未到也會列當前 active）。

- [ ] **Step 4: Commit**

```bash
cd ~/Desktop/projects/.claude && git add trials/active.md && git commit -m "chore(trials): add 4 wiki sibling trial-review entries @ 2026-07-13"
```

(注意：`~/Desktop/projects/.claude/` 是否獨立 git repo、依 user 設定。若非 git repo、跳過 commit。)

---

## Task 14: End-to-end smoke test

- [ ] **Step 1: 跑 `/daily-local` 看 5 wiki section 完整出現在 digest**

```
> /daily-local
```

Expected output (digest 部分):

```
# 每日本機分析 Digest — 2026-06-15

## memory
(既有 channel)

## wiki-candidates
(3-5 行濃縮)

## wiki-cross-link
(3-5 行濃縮)

## wiki-graduation
(3-5 行濃縮)

## wiki-lint
(3-5 行濃縮)

## wiki-stale
(3-5 行濃縮)

## recap
(其他 channel)
...

## 今天值得做的 1-2 件事
(既有 digest agent 收斂)
```

- [ ] **Step 2: 跑 `/wiki-actions` 模擬完整 flow**

```
> /wiki-actions
```

依 surface 結果模擬 user 回應：

1. **測試 approve**：「做 1」→ 確認 one-liner 跑成功 + dry-run 顯示 diff + 成功後刪 backup
2. **測試 decline**：「跳過 entity X」→ 確認 `wiki_<aspect>.<finding-key>: declined` 寫入正確 entity frontmatter
3. **測試 hold**：「hold entity Y 到 2026-09-01」→ 確認 `wiki_stale.override_until: 2026-09-01` 寫入

Expected:
- 合併 action queue 正確顯示 entity group
- Guard 1-4 在 destructive case 正確拒跑（手動構造 destructive one-liner 測試）
- Frontmatter 寫入前 show diff、user 確認才 apply

- [ ] **Step 3: Verify declined entity 隔次 sibling 跑跳過**

```bash
# 對剛 declined 的 entity 跑對應 sibling
bash ~/code/social-info/scripts/local-analysis/wiki-lint-daily.sh
grep "<declined entity slug>" ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-lint.md
```

Expected: declined entity 不出現在今日 report 主段（可能出現在「⏸ HOLD」段）。

- [ ] **Step 4: Verify cleanup**

```bash
find ~/.claude/wiki/ -name "*.wikiactions.bak.*"
ls ~/code/social-info/reports/local-analysis/$(date +%Y-%m-%d)-wiki-*.md
```

Expected:
- 0 個 backup file（session end cleanup）
- 5 個 wiki-* report file 全在

- [ ] **Step 5: Document E2E result（write to STATE.md or commit msg）**

把 smoke test 結果記到 `~/code/social-info/STATUS.md`（已存在，append 一段）或 commit msg：

```
End-to-end smoke test 結果（2026-06-15）：
- 5 sibling 都產 > 500 bytes report ✅
- digest 5 wiki section 連續顯示 ✅
- /wiki-actions approve flow OK ✅
- /wiki-actions decline flow OK ✅
- /wiki-actions hold flow OK ✅
- Guard 觸發 destructive 命令拒跑 ✅
- Declined entity 隔次跳過 ✅
- Backup cleanup OK ✅
```

---

## Task 15: Document feedback memory

**Files:**
- Create: `~/.claude/memory/reference_wiki_automation_setup_2026_06_15.md`
- (可選) Create: `~/.claude/memory/feedback_prerequisite_audit_before_spec_2026_06_15.md`

- [ ] **Step 1: 寫 reference memory 紀錄 setup**

`~/.claude/memory/reference_wiki_automation_setup_2026_06_15.md`：

```markdown
---
name: wiki-automation-setup-2026-06-15
description: 4 個 wiki daily sibling 接到 ~/code/social-info/scripts/local-analysis/ 體系 (lint/cross-link/graduation/stale) + /wiki-actions slash command (唯一寫入端、4 guards、合併演算法)、4 週 trial-review 觀察期到 2026-07-13
metadata:
  type: reference
---

# Wiki 自動化 setup (2026-06-15)

設計參考: ~/code/social-info/docs/superpowers/specs/2026-06-15-wiki-automation-design.md
實作 plan: ~/code/social-info/docs/superpowers/plans/2026-06-15-wiki-automation.md

## 4 sibling
- `wiki-lint-daily.sh`: orphan / broken / frontmatter / 重複
- `wiki-cross-link-daily.sh`: 平文 mention / backlink 漂移 / 新 entity (常用詞排除清單)
- `wiki-graduation-daily.sh`: 4 條判準 (high/verified/Changelog 1mo/引用≥3) + 升 CLAUDE.md vs rules/ 判定
- `wiki-stale-daily.sh`: lifecycle:stale + 久未 changelog (拿掉 stale-by 判準 - prereq A 0 命中)

## /wiki-actions
唯一寫入端、4 guards (黑名單 / path scope / dry-run / backup) + 合併演算法 (MAX priority group by entity)

## Trial-review
2026-07-13 看 adoption / noise / false-positive 比率，下回 evaluate 哪 sibling 保留 / 拿掉 / tune

## Schema 改動
`~/.claude/wiki/_schema.md` 加 4 個 propose-level 標記段 (wiki_lint / wiki_cross_link / wiki_graduation / wiki_stale)

## 關鍵 reframe
"記憶層 human-gate / wiki 層可彈性自動化" - 跟 14 次同槽否決 ([[memory-consolidation-rejection-landscape]]) 區別
"妥協 Obsidian 對 agent-only workflow cost ≈ 0" - 真實軸是套件 convention 對齊 + 接觸面 + 設計乾淨

## 相關
[[wiki-candidates-daily.sh]] (sibling pattern 模板)
[[memory-consolidation-rejection-landscape]]
[[reference_obsidian_second_brain_eval_2026_06_02]]
```

- [ ] **Step 2: 寫 feedback memory 紀錄 prerequisite audit 教訓（可選）**

`~/.claude/memory/feedback_prerequisite_audit_before_spec_2026_06_15.md`：

```markdown
---
name: prerequisite-audit-before-spec-2026-06-15
description: 寫 spec 前該 audit 既有 state 才能避免 assumption 破洞 - wiki-stale stale-by 判準在 65 entity 0 命中、需 spec 修正
metadata:
  type: feedback
---

# 寫 spec 前先 audit 既有 state

設計 wiki-stale sibling 時 spec 假設 `stale-by:` 是常用 frontmatter 欄位、判準 1 就是「stale-by 過期」。

**Why**: 2026-06-15 跑 prerequisite A audit 才發現 65 entity 中 64 個有 frontmatter、但 `stale-by:` **0 個用**。判準 1 = 空跑、整條 architecture 假設破洞。

**How to apply**: 寫涉及既有 state 的 spec 前，**腦中清單**先過：

1. 既有 state 各 dimension 的覆蓋率 / 健康度怎樣（grep / count 各欄位）
2. 我 spec 裡假設用的 X 欄位、實際 corpus 用嗎？
3. 我 spec 裡引用的 path / file，現在真的存在嗎？
4. 我 spec 裡依賴的工具 / pattern，現有 sibling 有用嗎？

只用「設計直覺」+「沒查既有 state」= 高機率 spec 假設破洞。

Prerequisite audit + brainstorm 自審 = spec quality gate。

## 相關
[[feedback_daily_automation_inventory_first]] (同 spirit、不同層級)
[[feedback_existing_equivalent_still_audit_gaps]]
```

- [ ] **Step 3: 更新 MEMORY.md index 加 reference pointer**

Read `~/.claude/memory/MEMORY.md`，在 standalone reference 段或對應 cluster 加 1 行 pointer：

```markdown
### Standalone reference
- [Wiki 自動化 setup (2026-06-15)](reference_wiki_automation_setup_2026_06_15.md) — 4 sibling (lint/cross-link/graduation/stale) + /wiki-actions 唯一寫入端 + trial-review 2026-07-13
```

(如果有對應 cluster 索引、考慮歸入該 cluster；參考 [[feedback_memory_cluster_maintenance]] 規則。)

- [ ] **Step 4: Commit**

```bash
cd ~/.claude && git add memory/reference_wiki_automation_setup_2026_06_15.md memory/feedback_prerequisite_audit_before_spec_2026_06_15.md memory/MEMORY.md && git commit -m "docs(memory): document wiki automation setup + prerequisite-audit-before-spec feedback"
```

---

## Self-Review Checklist

對照 spec 各 section、verify task coverage：

| Spec section | Task coverage |
|---|---|
| 1. 檔案組織 | Task 3 (rename) + Task 4/6/8/10 (4 sibling 新建) |
| 2.1 wiki-lint 判準 | Task 4 (完整 PROMPT 含 4 判準) |
| 2.2 wiki-cross-link 判準 | Task 6 (含常用詞排除清單實寫) |
| 2.3 wiki-graduation 判準 | Task 8 (含升 CLAUDE.md vs rules/ 判定規則) |
| 2.4 wiki-stale 判準 | Task 10 (拿掉 stale-by 判準) |
| 3. Recommendation block | Task 4/6/8/10 (PROMPT 都含 recommendation block 格式) |
| 4. Action queue | Task 4/6/8/10 (PROMPT 都含 action queue 段) |
| 5. Decline/hold/override | Task 1 (schema) + Task 12 (/wiki-actions 寫入邏輯) |
| 6. Dispatcher 接線 | Task 3/5/7/9/11 (CHANNELS array 改動) |
| 7. /wiki-actions | Task 12 (完整 command 含 4 guards) |
| 8. Schema 補充 | Task 1 |
| 9.1 PROMPT heredoc template | Task 4/6/8/10 內嵌完整 PROMPT |
| 9.2 合併演算法 | Task 12 step 1 含完整演算法 spec |
| 9.3 One-liner 安全 4 guards | Task 12 step 1 含 Guards 1-4 |
| Smoke test 計畫 | Task 14 (E2E test) |
| Trial-review 排程 | Task 13 |
| Document learnings | Task 15 (reference + feedback memory) |

**Type consistency check**: ✅
- Sibling key names: candidates / cross-link / graduation / lint / stale 一致
- Channel `key` 跟 wrapper filename 跟 report filename 都一致
- Frontmatter 標記 `wiki_lint:` / `wiki_cross_link:` / `wiki_graduation:` / `wiki_stale:` 一致 (snake_case)

**Placeholder scan**: ✅ 無「TBD」「TODO」「Similar to Task N」placeholder。

**Spec gap**:
- ⚠️ Spec 中 wiki-lint 判準寫 4 條（含「重複 entity」相似度 70%），但實作上 entity TL;DR 相似度判定靠 LLM 自己判（無 deterministic threshold）。這是 implementation-phase 細節、不算 plan gap。
- ⚠️ Backup cleanup 機制 spec 寫 session end 跑，plan Task 12 step 1 寫進 command markdown。但 CC session end 是 user 關閉、command 是 invoke-time——cleanup 實際 trigger 點需要實作時驗證（可能要改成 invoke `/wiki-actions` 開頭跑 cleanup 1 天前的 backup）。

---

## 開始實作

Plan complete and saved to `~/code/social-info/docs/superpowers/plans/2026-06-15-wiki-automation.md`.

15 tasks 涵蓋：
- 預備（Task 1-3）：schema + frontmatter 補 + rename
- 4 sibling 寫作（Task 4, 6, 8, 10）+ 各自接 workflow（Task 5, 7, 9, 11）
- `/wiki-actions` slash command（Task 12）
- Trial-review 設定（Task 13）
- E2E smoke test（Task 14）
- Memory documentation（Task 15）

預估總工時：4-6 小時（依 PROMPT heredoc tune 時間）。
