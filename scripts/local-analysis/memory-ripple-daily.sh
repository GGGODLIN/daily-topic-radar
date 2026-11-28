#!/bin/bash
# memory-ripple-daily.sh — 每日增量記憶波及檢查（M-1）的回寫規則。
#
# 由 ~/.claude/workflows/local-analysis.js 透過子 workflow ~/.claude/workflows/memory-ripple-check.js 執行：
# 1. memory-ripple-batches.py prepare 讀 ~/.claude/logs/memory-channel-state.json 的 last_ripple_ts，
#    對 memory-ripple-audit.jsonl 中所有 fired=true 且 ts > last_ripple_ts 的事件一次讀庫算齊兄弟檔並按 20 檔切批（不截斷上限；>=100 檔標警告）。
# 2. 0 件時直接靜默跳過、不開比對 agent；有件數時分波（峰值 5 並行）逐批比對「仍把舊前提當現行」。
# 3. 報告由 main 落檔至 /Users/linhancheng/code/social-info/reports/local-analysis/<date>-memory-ripple.md 且確認非空後，
#    才依 deferred_writeback 推進 last_ripple_ts。若任一批掃描失敗，deferred_writeback 為空字串、游標不推進，下次重查。

cat <<'EOF'
# memory-ripple-daily 回寫規則（供 main 落檔後對照執行 deferred_writeback）

當 `<date>-memory-ripple.md` 報告已成功寫入磁碟並確認非空後，執行 `deferred_writeback` 內提供的固定指令：

```bash
python3 /Users/linhancheng/code/social-info/scripts/local-analysis/memory-ripple-batches.py commit-cursor \
  --state '/Users/linhancheng/.claude/logs/memory-channel-state.json' \
  --ts '<本次 prepare 回傳的 cutoff_ts>' \
  --manifest '/Users/linhancheng/code/social-info/reports/local-analysis/memory-ripple/work-<date>/manifest.json' \
  --report '/Users/linhancheng/code/social-info/reports/local-analysis/<date>-memory-ripple.md'
```

只允許推進 `last_ripple_ts`；報告落檔失敗、檔案為空、或 `deferred_writeback` 是空字串時，不得推進游標。
報告列出的「過時記憶候選」只做盤點、不自動改檔；修補記憶前由 main 或使用者確認後再改原檔。
EOF
