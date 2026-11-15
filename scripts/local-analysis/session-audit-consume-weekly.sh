#!/bin/bash
# session-audit-consume-weekly.sh — 每週六（weekly-sat）session-audit 無門檻全量分批消費 channel。
#
# 由 ~/.claude/workflows/local-analysis.js（當 weekday === 6 時）透過子 workflow
# ~/.claude/workflows/session-audit-consume.js 執行：
# 1. 依 state.sqlite 的列級 consumed_at IS NULL 搭配雙軌就緒檢查（is_source_ready）抽取新發現、寫入 pending-consume.json 預合併組並分批；
# 2. 分批全量讀取 -> 中間層現況核對 -> 頂層合成完整報告正文；
# 3. 報告由 main 落檔至 /Users/linhancheng/code/social-info/reports/local-analysis/<date>-session-audit-consume.md 後，
#    才依 deferred_writeback 執行 commit-cursor：先將報告內嵌的全域環境待決項（session-audit-ledger-findings）
#    透過 ledger-reconcile.py 冪等寫入 pending-actions.jsonl，確認入帳成功後才回寫已消費列的 consumed_at、更新或清除 pending-consume.json 並記錄 consume-cursor.json（不得提前提交）。

cat <<'EOF'
# session-audit-consume-weekly 回寫規則（供 main 落檔後對照執行 deferred_writeback）

當 `<date>-session-audit-consume.md` 報告已成功寫入磁碟並確認非空後，執行 `deferred_writeback` 內提供的固定指令（該指令會先將報告尾端 `<!-- session-audit-ledger-findings: ... -->` 的合格全域環境待決項寫入 `pending-actions.jsonl`，確認成功後才依本次 `pending-consume.json` 標記已消費列的 `consumed_at`、更新或刪除 `pending-consume.json`，並寫入 `consume-cursor.json`）：

```bash
python3 /Users/linhancheng/code/social-info/scripts/local-analysis/session-audit-consume.py commit-cursor \
  --state '/Users/linhancheng/code/social-info/reports/local-analysis/session-audit' \
  --finding-id <本次已讀完的最大 finding_id> \
  --rule-tag-id <本次已讀完的最大 rule_tag_id> \
  --date '<date>' \
  --report '/Users/linhancheng/code/social-info/reports/local-analysis/<date>-session-audit-consume.md'
```

只允許透過上述 `commit-cursor` 指令更新已消費列的 `consumed_at`、`pending-consume.json` 與 `consume-cursor.json`（上限 ID、日期與報告路徑）；若報告落檔或帳本匯入失敗，不得執行回寫。
EOF
