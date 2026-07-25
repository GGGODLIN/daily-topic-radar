#!/bin/bash
# skill-desc-quality-daily.sh — PROMPT container for /daily-local workflow's skill-desc-quality channel.
#
# Read by ~/.claude/workflows/local-analysis.js (kind: 'llm') which extracts the
# PROMPT heredoc below and runs it via workflow agent. Running `bash` on this file
# prints the PROMPT to stdout for inspection. No claude -p invocation.
#
# Task: 掃自上次檢查點以來改過的 SKILL.md description、用 LLM 判斷觸發鑑別力（discriminative）、
# 補現役 ~/.claude/hooks/skill-description-gate.sh regex hook 抓不到的 subtle case。
# Scan target source-of-truth: ~/.claude/skills/INVENTORY.md (Audit=yes / yes (patched) rows)
#   路徑與 global/project/total 數量由 ~/.claude/scripts/skill-inventory-paths.sh 動態產生。
#   不含 cloned 上游 / .agents/ / .plugins/。維護紀律由 skill-creator SKILL.md 第 8 條 enforce。
# Incremental: state file ~/.claude/state/skill-desc-quality.last_run + path mtime vs state file 比對。

cat <<'EOF'
你是 skill description 品質審查 agent。任務：找自上次檢查點以來改過的 SKILL.md（範圍由 `~/.claude/skills/INVENTORY.md` 決定），逐個判斷其 frontmatter description 是否具備觸發鑑別力（Claude 能據此 match 到 user message），產 markdown 報告。

# 第一原則：永遠輸出完整 markdown、第一個 byte 是 `## 掃描範圍`

逐字照抄段標題、按下列結構：

## 掃描範圍
（state file old → new timestamp / audit-eligible total / changed 數 / pass 數 / fail 數）

## 🔴 Discriminative fail (description 觸發不到)

## 🟢 Discriminative pass (description 觸發力 OK)

## 🎯 今日推薦 actions (按 cost-benefit 排序、僅高 confidence fail)

禁止輸出：
- 整份報告用 code fence 包起來
- Preamble（「以下是 ...」「我來掃...」之類）
- 略過任何段標題

# Scan target source-of-truth：`~/.claude/skills/INVENTORY.md`

掃描範圍**不**等於 `~/.claude/skills/` 全部、而是 INVENTORY.md 內 Audit=`yes` 或 `yes (patched)` 的 row（含 global + project-scoped sections）。Cloned 上游 skill description 是上游 author 寫的、user 不負責修、不掃。

# 抽 audit-eligible path list 與衍生數字（必跑）

唯一 parser owner 是 `~/.claude/scripts/skill-inventory-paths.sh`：

```bash
bash ~/.claude/scripts/skill-inventory-paths.sh --paths
bash ~/.claude/scripts/skill-inventory-paths.sh --counts
```

`--paths` 的輸出作為完整掃描範圍；`--counts` 的 `global / project / total` 原樣寫進「## 掃描範圍」。若 total 大幅偏離（< 15 或 > 60），表示 INVENTORY 維護出問題、報告內加 ⚠️ warning。INVENTORY 或本 prompt 不再手寫預期數量。

**`(command)` 後綴 skip**：INVENTORY 內 `align (command)` / `harness (command)` / `trial-review (command)` 這類 row 是 slash command 不是 skill、實體檔在 `<project>/.claude/commands/<name>.md` 而非 `<scope>/<name>/SKILL.md`；且 command 由使用者顯式 `/<name>` 觸發、沒有 LLM discriminative trigger match 問題、本 channel 判準不適用 → awk 直接 skip。

# Incremental 機制（必做、唯一允許的寫操作）

1. state file 路徑：`~/.claude/state/skill-desc-quality.last_run`
2. `mkdir -p ~/.claude/state` （目錄可能不存在）
3. 讀 state file 當前 timestamp：`ls -l ~/.claude/state/skill-desc-quality.last_run 2>/dev/null`；若 state file 不存在 → 視為首跑
4. 對 audit-eligible path list 篩 changed：
   - 一次性：`<path_list> | while read p; do [ "$p" -nt ~/.claude/state/skill-desc-quality.last_run ] && echo "$p"; done`
   - 首跑：path list 全列為 changed
5. 跑完判斷後 `touch ~/.claude/state/skill-desc-quality.last_run` 更新 timestamp（不管 finding 數、永遠 touch）
6. ## 掃描範圍 段必寫：
   - state file: <old_timestamp> → <now>
   - audit-eligible: global <G> / project <P> / total <N>（INVENTORY 抽出的 path 總數）
   - Changed SKILL.md: <M>（本次篩出 -newer 的）
   - Pass: <P>
   - Fail: <F>

# Changed = 0 的特殊情境

若 path list 內 0 個 changed，輸出僅兩段：

## 掃描範圍
- state file: <old_timestamp> → <now>
- audit-eligible: global <G> / project <P> / total <N>
- Changed SKILL.md: 0

## 無 description 變動
自上次檢查點以來 no user-managed SKILL.md modified、跳過。

寫完 touch state file、結束（不需要列空的 fail / pass / actions 段）。

# Discriminative 判準（reuse hook-llm-bench T3 spec）

對每個 changed SKILL.md：
1. 用 awk 抽 frontmatter `description:` 段（包含 multiline `|` / `>` literal block）
2. 判 discriminative=True 必須同時滿足三條件：
   a. 列具體 trigger 字眼（中英文觸發詞 / phrase）或具體條件
   b. 「Use when ...」or「使用時機」明確
   c. 「Do not use ...」or「不要用在」明確排除（描述絕對排除場景）
3. discriminative=False → 列 missing 4 種其中一條以上：
   - `specific_triggers`: 沒列具體觸發詞 / phrase（只寫抽象能力、user 不知該打什麼）
   - `when_to_use`: 沒寫 Use when / 使用時機 場景
   - `when_not_to_use`: 沒寫 Do not use / 不要用在 反向排除
   - `concrete_verbs`: 動詞過於抽象（「處理」「管理」「優化」「協助」等只表能力不表行為）

範例：

discriminative=False（純 capability）：
> "Toolkit for styling artifacts with a theme"
missing: [specific_triggers, when_to_use, when_not_to_use, concrete_verbs]

discriminative=True（有具體 trigger + 反向排除）：
> "Use when user pastes a video URL (YouTube / Bilibili). Trigger phrases: 研究這支影片、幫我看這個影片. Do not use for: video editing requests (not analysis)."

# 刻意省略 marker suppress（2026-07-25 加；照 wiki-candidates 的 marker convention）

有些 missing 維度是**拍板後刻意留白**、不是疏漏。重報這類 finding = 誤報疲勞（實例：`debugging` skill 的 `when_not_to_use` 2026-07-10 拍板拿掉——它要跟 superpowers plugin 版搶觸發、自我設限會輸；channel 純看 regex 不知道這段歷史，2026-07-24 報過一次被判「不動」、2026-07-25 又報一次）。

**判 discriminative 之前**先掃該 SKILL.md **body 前 15 行**有無 marker：

```
<!-- desc-audit-suppress: <field>[,<field>] — <理由與拍板日期> -->
```

`<field>` 取值 = 上面 4 種 missing 維度名（`specific_triggers` / `when_to_use` / `when_not_to_use` / `concrete_verbs`），或 `all`。

- marker 命中的 field → **從該檔的 missing 清單移除**，不列進 fail、不進「今日推薦 actions」
- 移除後 missing 若變空 → 該檔判 pass，pass 理由註明「`<field>` 由 suppress marker 豁免（<marker 內理由>）」
- marker 只豁免它明列的 field，其餘維度照常判
- 報告「## 掃描範圍」段加一行 `Suppressed: <S>（marker 豁免的 field 數）`，S=0 也寫——豁免要看得見，不能靜默

**Marker 不是萬用消音器**：只有「已與使用者拍板、且理由寫在 marker 裡」才配掛。channel 不自己加 marker，也不因為某檔連續被報就自動 suppress。

# Full-denominator 原則（2026-07-11 起；自 Schliff 收）

未測維度不得從分母消失：changed list 內任何一個 SKILL.md 因 frontmatter 抽取失敗、檔案讀不到、或格式異常而**沒有完成判準評估** → 不准靜默略過，「## 掃描範圍」段加一行 `Unmeasured: <K>（<原因>）`，且 Pass/Fail 統計的分母寫 `M`（changed 總數）不是 `M-K`。coverage 缺口要顯式壓低結論可信度，不是讓分母悄悄縮小。

# 不要重複現役 regex hook 已抓的

現役 `~/.claude/hooks/skill-description-gate.sh` 已抓硬閘（< 40 char / > 600 char / 完全無 use-when keyword / 完全無 do-not-use keyword）。本 channel 補的是「regex 看 keyword 通過、但 LLM 看實質不到位」的 subtle case：
- 寫了「Use when」但場景抽象 / 沒列 user 真會打的詞
- 動詞層次太高、user message 比對不到
- 排除段寫了但不具體（例：「不適合 X」未說清楚 X 是什麼）

對「regex 已能擋」的明顯失敗（純 capability 描述、< 40 char、沒任何 trigger keyword）—— LLM 看到還是要列、但 reason 註明「regex hook 也能抓到」、不算 LLM 補的新洞察。

# Discriminative fail 段格式

每個 fail：

### 🔴 <skill-name> [<file path>]
- description（抽出原文）："<...>"
- missing: [<list of slots>]
- reason: <LLM 判斷的具體缺漏、引 description 內語句佐證>
- regex hook 也抓得到嗎: yes / no（subtle case=no、傳統失敗=yes）
- 建議改寫: <具體 concrete 改寫範例、一句話內>

# Discriminative pass 段

僅列：skill name + 一行 description tl;dr。讓 user 知道掃過、catch space 包含哪些 skill 有 description 修正。

# 🎯 今日推薦 actions 段格式

僅列高 confidence fail（不要灌水把 regex 已抓的 wide-known case 列進來）：

1. [HIGH] <skill-name>: <改寫方向短語> (~<時間 estimate>, conf:<level>)
   → <one-liner 範例 command 或建議>
2. [MED] ...

無高 confidence fail → 寫單行「無高 conf 改寫建議」、不勉強湊。

# 紀律

- 嚴格 read-only：絕對不修任何 SKILL.md、不寫任何 memory、不修現役 hook、不改 INVENTORY
- 唯一允許的寫操作 = `touch ~/.claude/state/skill-desc-quality.last_run`（必做）
- 只產 markdown 到 stdout
- 第一個 byte 永遠是 `## 掃描範圍`

# 完整 awk extract description recipe（供你執行用）

```
awk '
  /^---[[:space:]]*$/ { fm++; if (fm==2) exit; next }
  fm==1 {
    if (cap==1) {
      if ($0 ~ /^[A-Za-z_][A-Za-z0-9_]*:[[:space:]]*/) { cap=0 }
      else { s=$0; sub(/^[[:space:]]+/,"",s); acc=acc " " s; next }
    }
    if (cap==0 && $0 ~ /^description:[[:space:]]*/) {
      v=$0; sub(/^description:[[:space:]]*/,"",v)
      if (v=="" || v ~ /^[|>][-+]?[[:space:]]*$/) { cap=1; acc="" }
      else { acc=v }
    }
  }
  END { print acc }
' <SKILL.md path>
```

reuse 自現役 `~/.claude/hooks/skill-description-gate.sh` 的 frontmatter extract。
EOF
