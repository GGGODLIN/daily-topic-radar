#!/bin/bash
# memory-conflict-daily.sh — 每日記憶筆記互相矛盾檢查 channel 的回寫規則。
#
# 由 ~/.claude/workflows/local-analysis.js 透過子 workflow ~/.claude/workflows/memory-conflict-check.js 執行：
# 1. memory-conflict.py prepare 依 cursor.json 的時間水位線挑出之後改過的筆記，連同相關筆記切批；
# 2. Gemini Pro（gemini-pro-high）與 SWE-2（swe2max-pool）平行掃每一批，GPT-6.1 Sol 讀原檔合併判定；
# 3. 報告由 main 落檔至 /Users/linhancheng/code/social-info/reports/local-analysis/<date>-memory-conflict.md 後，
#    才依 deferred_writeback 推進水位線。掃描或判定沒做完時 deferred_writeback 是空字串，下一輪會重查同一批筆記。
# 全量掃描用同一支 workflow 帶 scope=all，不碰水位線。

cat <<'EOF'
# memory-conflict-daily 回寫規則（供 main 落檔後對照執行 deferred_writeback）

當 `<date>-memory-conflict.md` 報告已成功寫入磁碟並確認非空後，執行 `deferred_writeback` 內提供的固定指令：

```bash
python3 /Users/linhancheng/code/social-info/scripts/local-analysis/memory-conflict.py commit-cursor \
  --state '/Users/linhancheng/code/social-info/reports/local-analysis/memory-conflict' \
  --epoch <本次 prepare 回傳的 cursor_candidate> \
  --date '<date>' \
  --report '/Users/linhancheng/code/social-info/reports/local-analysis/<date>-memory-conflict.md'
```

只允許推進這個時間水位線；報告落檔失敗、或 deferred_writeback 是空字串時，不得推進 cursor.json。
報告裡「需要更正」的項目只是候選，改筆記前由動手的 session 再讀一次兩個原檔確認現況。
EOF
