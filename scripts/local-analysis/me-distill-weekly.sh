#!/bin/bash
# 蒸餾游標由 main 在落檔後推進，讓落檔失敗時的增量仍可在下輪重播。

cat <<'EOF'
# me-distill 週更回寫規則（供 main 落檔後對照執行 deferred_writeback）

當 `/Users/linhancheng/code/social-info/reports/local-analysis/<date>-me-distill.md` 已成功寫入磁碟並確認非空後，只允許執行 `deferred_writeback` 內逐字相符的固定指令：

```bash
python3 /Users/linhancheng/Desktop/projects/me-distill/scripts/manifest_tool.py --profile /Users/linhancheng/Documents/me-profile commit_distill --tag local-analysis-<date>
```

報告落檔失敗、本輪有子來源失敗告警，或 `deferred_writeback` 為空字串時，不得推進蒸餾游標。
EOF
