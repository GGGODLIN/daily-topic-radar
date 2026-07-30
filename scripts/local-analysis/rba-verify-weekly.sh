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

## Step 1 — 取得確定性樣本（照跑，不自行重寫排序）

```bash
sample_json="$(/Users/linhancheng/code/social-info/scripts/local-analysis/rba-verify-sample.sh)"
printf '%s\n' "$sample_json" | jq .
```

helper 會找每個 session 在近 7 天內第一個真正 invoke `research-before-answer`、排除 ledger 已抽驗 UUID，以 session basename 排序，再取第一個／下中位／最後一個；不足 3 個全取。`.eligible` 是去重後候選數，`.samples[]` 同時保留 UUID、選定 invoke timestamp 與完整 path。0 個時直接輸出「本週無 invoke」報告收工。

## Step 2 — 固定單一 invoke 與證據時間窗

每個 session 只評 `.samples[].invoke` timestamp 指定的 research-before-answer invoke：定位同 timestamp 的真實 `Skill` tool_use，其前一則相關 user 訊息是題目；後續第一個完整回答該題目、且不是進度回報的 assistant 文字是受評答案。同 session 其他 invoke、後續 user 題目與實作階段一律不納入。

可計分證據只限該 invoke 發生後、受評答案產生前的 tool result。遇到 Workflow 時，從父 session 的 runId 追到同專案 session 目錄下 `subagents/workflows/<runId>/agent-*.jsonl`，核對真實 tool_use 與 tool_result；父 session 的 Workflow 摘要本身不算 R1 證據。回答產生後的工具結果不得倒灌成回答當下已有的證據。

## Step 3 — 逐 session 對 rubric（每條 PASS/FAIL + 一句證據引述；偏置找違規、無法確認就 FAIL）

先用 jq 定位選定 invoke、題目、答案及時間窗內工具序列；只有需要核對的段落才細讀。判分前先列出受評答案中所有會改變使用者決策或直接回答使用者問題的具體主張（通常 1-5 條），每條都附答案原句、答案內來源指針或 `NONE`、時間窗內支持／衝突的 tool result 或 `NONE`。不得只抽查最容易通過的 1 至 2 點；若主張很多，可把純背景細節略過，但使用者直接問的條件與答案用來推薦／排除選項的理由不可略過。

- **R1【有真的查】**：時間窗內有沒有實際第一手查證動作（WebSearch / WebFetch / chrome navigate / gh api / curl / context7 / 讀官方 doc 任一）？nested Workflow 只有在 agent transcript 看到真實 tool result 才算。invoke 完直接憑訓練資料作答 = FAIL。
- **R2【答案附出處】**：逐條檢查上述主張是否在受評答案附來源指針（URL / 明確文件名 / 檔案路徑 / 指令輸出）。不要求每句各放一個 URL；但任一會改變決策或直接回答問題的主張只有結論、沒有可追溯指針，R2 = FAIL。
- **R3【查答一致】**：逐條比對上述主張與時間窗內 tool result。任一主張與查證內容矛盾、來源互相衝突卻未釐清、或查證未覆蓋卻被當成選擇理由（過度外推），R3 = FAIL；使用「應該／可能」不能消除來源衝突。跨來源合併帳號／方案／額度池等身分時，必須找到共同 account id、plan type、token subject 或等價 identifier；只有名稱相似或 memory 線索不得視為同一實體。

## Step 4 — Ledger 寫回（read-only 的例外）

每個抽驗 session append 一行到 `/Users/linhancheng/code/social-info/reports/local-analysis/rba-verify-ledger.jsonl`：
`{"date":"<今日>","session":"<不含 .jsonl 的 UUID>","invoke":"<Skill tool_use id 或時間戳>","R1":"PASS|FAIL","R2":"PASS|FAIL","R3":"PASS|FAIL","note":"<FAIL 時一句摘要，PASS 留空>"}`
append 前以去除 `.jsonl` 後的 UUID 比對、有就跳過。先寫 ledger 再輸出報告。

## 輸出格式

第一個 byte 是 `## 掃描範圍`。段落：

```
## 掃描範圍
（近 7 天候選 session 數 / 本週抽驗 3 個清單 / ledger 累計抽驗數）

## 抽驗結果
每 session 一段：session 短碼 + 研究的題目一句話 +「核心主張盤點」（每條列答案原句／來源指針或 NONE／tool result 支持、衝突或 NONE）+ R1/R2/R3 判定與證據引述

## 判讀
全 PASS → 一行「本週抽驗 3/3 乾淨」。
任一 FAIL → ⚠️ 開頭列出（digest 端不得濃縮省略），附該 FAIL 的 quote。
```

**Escalation 規則**：把今日日期減 7 日，只讀絕對路徑 `/Users/linhancheng/code/social-info/reports/local-analysis/<今日減 7 日>-rba-verify.md`；檔案不存在就不算連續週。本週任一 FAIL 時以 `⚠️` 開頭；前週報告只有在「抽驗結果」中的 R1／R2／R3 至少一格判定為 FAIL，或「判讀」有 `⚠️`／`🚨` 警告行時，才算前週失敗；「無 FAIL」或「0 FAIL」等敘述不算前週失敗。若前週也失敗，本週改標 `🚨`，並在判讀段建議「升級 research-before-answer SKILL.md inline sampling verify（原 reminder 否決的方案重啟）」——只建議、由使用者拍板。

紀律：嚴格 read-only（唯一例外 = ledger append）。不要 preamble、不要 code fence 包整份報告。
EOF
