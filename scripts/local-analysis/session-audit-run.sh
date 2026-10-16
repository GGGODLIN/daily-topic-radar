#!/bin/bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
DIR="$(cd "$(dirname "$0")" && pwd -P)"
REPO="$(cd "$DIR/../.." && pwd -P)"
PY="${SESSION_AUDIT_PYTHON:-/opt/homebrew/bin/python3}"
STATE="${SESSION_AUDIT_STATE:-$REPO/reports/local-analysis/session-audit}"
PROJECTS="${SESSION_AUDIT_PROJECTS:-$HOME/.claude/projects}"
STARTED_AT="${SESSION_AUDIT_STARTED_AT:?SESSION_AUDIT_STARTED_AT must be the deployment cutoff}"

# 平常只追新增 session：走 Magpie 的 audit-chain、同時 10 個。鏈依序是 Argon high → SWE-2 high → MiMo → DeepSeek V4.1，
# 前兩顆隨時可能斷供，斷了由 Magpie 往下接；不再走 free 鏈（使用者 2026-10-16 決定，依據是同日真實工作量盲評，
# local-llm-gateway .scratch/real-workload-eval/report.md）。開回填批次時改走三條平行直連線各 5 槽，批次進 review 後下一輪自動退回鏈。
# 回填不用 Argon（使用者 2026-10-17 決定）：它跟互動 session 的 Argon 共用同一把 Google key，回填佔了分析器 Argon 用量的 84%，
# 而且直連線沒有鏈可往下接，429 只能由分析器自己換線。
# Grok 週額度用到 50% 就讓出（GROK_PAUSE_PERCENT），名額空著不壓到其他線。
"$PY" "$DIR/session-audit.py" run \
  --projects "$PROJECTS" \
  --state "$STATE" \
  --started-at "$STARTED_AT" \
  --relay-config "${SESSION_AUDIT_RELAY_CONFIG:-$HOME/.cli-proxy-api/config.yaml}" \
  --keys-file "${SESSION_AUDIT_KEYS_FILE:-$HOME/.cli-proxy-api/keys.env}" \
  --direct-legs "${SESSION_AUDIT_DIRECT_LEGS-audit-chain:10}" \
  --backfill-legs "${SESSION_AUDIT_BACKFILL_LEGS-swe2-high:5,mimo26-pool:5,grok-4.7:5}" \
  --max-sessions "${SESSION_AUDIT_MAX_SESSIONS:-10}"
# 分析器的發現量大且未經 review；放在 ~/.claude/friction 外，避免跟其他機制的摩擦混在一起、灌爆 trial-review。
"$PY" "$DIR/session-audit.py" promote \
  --state "$STATE" \
  --friction-root "${SESSION_AUDIT_FRICTION_ROOT:-$STATE/friction}"
