#!/bin/bash
# wiki-cross-link-daily.sh — PROMPT container for /daily-local workflow's wiki-cross-link channel.
#
# Read by ~/.claude/workflows/local-analysis.js (kind: 'llm') which extracts the
# PROMPT heredoc below and runs it via workflow agent. Running `bash` on this file
# prints the PROMPT to stdout for inspection. No claude -p invocation.

cat <<'EOF'
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
4. **Alias-aware mention**（2026-07-11 起）: entity 的別名也算平文 mention——別名來源限兩處：frontmatter `aliases:` 欄位（若有）、entity 檔首段明寫的「舊名 / 又稱 / 前身」（例 cn-model-swap-landscape 是 llm-model-landscape 前身）。命中 → propose 補 canonical slug 的 `[[]]`。別名同樣過 False positive 防護（短於 5 字元 / 常用詞不 propose）

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
