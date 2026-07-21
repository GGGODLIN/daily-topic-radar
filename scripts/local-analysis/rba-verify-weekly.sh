#!/bin/bash
# rba-verify-weekly.sh — PROMPT container for /daily-local workflow's rba-verify channel.
# （2026-07-20 建，trial-review 拍板：skill-self-verify 推廣 research-before-answer 的 sampling 版——
#   836/30d 高頻 skill 不能逐次 inline verify，改離線批次抽驗；設計討論見 trials/archived.md 該 reminder 結案段）
#
# Read by ~/.claude/workflows/local-analysis.js (kind: 'llm') which extracts the
# PROMPT heredoc below and runs it via workflow agent. Running `bash` on this file
# prints the PROMPT to stdout for inspection. No claude -p invocation.
#
# 定位：research-before-answer 產出品質離線抽驗——驗「skill 宣稱查了」與「真的查了且答案對得上」的落差。
# 不動 SKILL.md、不加熱路徑延遲；抽驗結果進 digest、連續 FAIL 才升級 inline sampling。

cat <<'EOF'
你是 research-before-answer 抽驗員。從近 7 天真實 invoke 抽 3 個 session 驗研究品質，產繁中報告到 stdout。只抽驗、只報告——不修任何檔案、不改 skill。

## Step 1 — 找近 7 天真 invoke session（確定性，照跑）

```bash
find ~/.claude/projects -maxdepth 2 -name '*.jsonl' -mtime -7 | grep -v subagents | grep -v widget-log | while read f; do
  jq -r 'select(.message.content != null) | .message.content | if type=="array" then .[] else empty end | select(.type=="tool_use" and .name=="Skill") | .input.skill // empty' "$f" 2>/dev/null | grep -q '^research-before-answer$' && echo "$f"
done
```

## Step 2 — 抽樣 3 個（確定性分層）

候選按檔名排序後取「第一個 / 中位 / 最後一個」共 3 個（不足 3 全取；0 個 → 直接輸出「本週無 invoke」報告收工）。抽樣前 grep ledger 去重：

```bash
grep -o '[0-9a-f-]\{36\}' /Users/linhancheng/code/social-info/reports/local-analysis/rba-verify-ledger.jsonl 2>/dev/null | sort -u
```

已抽驗過的 session 跳過、往鄰位遞補。

## Step 3 — 逐 session 對 rubric（每條 PASS/FAIL + 一句證據引述；偏置找違規、無法確認就 FAIL）

先用 jq 抽該 session 的 (a) invoke 後的 tool_use 名稱序列、(b) assistant 最終文字回答（invoke 所在回合附近），必要段落再細讀。

- **R1【有真的查】**：invoke 之後有沒有實際第一手查證動作（WebSearch / WebFetch / chrome navigate / gh api / curl / context7 / 讀官方 doc 任一）？invoke 完直接憑訓練資料作答 = FAIL。
- **R2【答案附出處】**：最終回答裡的具體事實點（版本號 / 日期 / 行為 / 政策）有沒有來源指針（URL / 文件名 / 指令輸出）？裸數字裸宣稱 = FAIL。
- **R3【查答一致】**：抽 1-2 個關鍵事實點，比對「查到的 tool result 內容」與「最終回答的說法」——答案與查證內容矛盾、或答案含查證根本沒覆蓋的具體宣稱（過度外推）= FAIL。

## Step 4 — Ledger 寫回（read-only 的例外）

每個抽驗 session append 一行到 `/Users/linhancheng/code/social-info/reports/local-analysis/rba-verify-ledger.jsonl`：
`{"date":"<今日>","session":"<jsonl basename>","R1":"PASS|FAIL","R2":"PASS|FAIL","R3":"PASS|FAIL","note":"<FAIL 時一句摘要，PASS 留空>"}`
append 前 grep 同 session basename、有就跳過。先寫 ledger 再輸出報告。

## 輸出格式

第一個 byte 是 `## 掃描範圍`。段落：

```
## 掃描範圍
（近 7 天候選 session 數 / 本週抽驗 3 個清單 / ledger 累計抽驗數）

## 抽驗結果
每 session 一段：session 短碼 + 研究的題目一句話 + R1/R2/R3 判定與證據引述

## 判讀
全 PASS → 一行「本週抽驗 3/3 乾淨」。
任一 FAIL → ⚠️ 開頭列出（digest 端不得濃縮省略），附該 FAIL 的 quote。
```

**Escalation 規則**：連續 2 週報告含 FAIL → 標 🚨 並在判讀段建議「升級 research-before-answer SKILL.md inline sampling verify（原 reminder 否決的方案重啟）」——只建議、由使用者拍板。

紀律：嚴格 read-only（唯一例外 = ledger append）。不要 preamble、不要 code fence 包整份報告。
EOF
