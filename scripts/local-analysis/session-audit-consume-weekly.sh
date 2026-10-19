#!/bin/bash
# session-audit-consume-weekly.sh — 每週六（weekly-sat）session-audit 無門檻全量分批消費 channel。
#
# 由 ~/.claude/workflows/local-analysis.js（當 weekday === 6 時）透過子 workflow
# ~/.claude/workflows/session-audit-consume.js 執行：
# 1. 依 consume-cursor.json 的自增 ID 水位線（last_finding_id / last_rule_tag_id）抽取新發現並分批；
# 2. 分批全量讀取 -> 中間層現況核對 -> 頂層合成完整報告正文；
# 3. 報告由 main 落檔至 /Users/linhancheng/code/social-info/reports/local-analysis/<date>-session-audit-consume.md 後，
#    才依 deferred_writeback 執行 commit-cursor 推進水位線（不得提前提交 checkpoint）。

cat <<'EOF'
# session-audit-consume-weekly 回寫規則（供 main 落檔後對照執行 deferred_writeback）

當 `<date>-session-audit-consume.md` 報告已成功寫入磁碟並確認非空後，執行 `deferred_writeback` 內提供的固定指令以推進 ID 水位線：

```bash
python3 /Users/linhancheng/code/social-info/scripts/local-analysis/session-audit-consume.py commit-cursor \
  --state '/Users/linhancheng/code/social-info/reports/local-analysis/session-audit' \
  --finding-id <本次已讀完的最大 finding_id> \
  --rule-tag-id <本次已讀完的最大 rule_tag_id> \
  --report '/Users/linhancheng/code/social-info/reports/local-analysis/<date>-session-audit-consume.md'
```

只允許推進上述兩個整數水位線與報告路徑；若報告落檔失敗，不得執行 commit-cursor。
EOF
