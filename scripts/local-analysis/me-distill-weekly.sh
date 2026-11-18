#!/bin/bash
# 蒸餾游標由 main 在落檔後推進，讓落檔失敗時的增量仍可在下輪重播。

cat <<'EOF'
# me-distill 週更回寫規則（供 main 落檔後對照執行 deferred_writeback）

當 `/Users/linhancheng/code/social-info/reports/local-analysis/<date>-me-distill.md` 已成功寫入磁碟並確認非空後，只允許執行 `deferred_writeback` 內逐字相符的固定指令：

```bash
python3 /Users/linhancheng/Desktop/projects/me-distill/scripts/manifest_tool.py --profile /Users/linhancheng/Documents/me-profile commit_distill --tag local-analysis-<date>
```

報告落檔失敗時，不得推進蒸餾游標，並應立即執行對應標籤的快照還原以回復本輪已改寫的分面與個人摘要：

```bash
python3 /Users/linhancheng/Desktop/projects/me-distill/scripts/manifest_tool.py --profile /Users/linhancheng/Documents/me-profile bundle_rollback --tag local-analysis-<date>
```

本輪若有子來源失敗告警或 `deferred_writeback` 為空字串時，同樣不得推進蒸餾游標。
EOF
