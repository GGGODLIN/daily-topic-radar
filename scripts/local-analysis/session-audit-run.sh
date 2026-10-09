#!/bin/bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
DIR="$(cd "$(dirname "$0")" && pwd -P)"
REPO="$(cd "$DIR/../.." && pwd -P)"
PY="${SESSION_AUDIT_PYTHON:-/opt/homebrew/bin/python3}"
STATE="${SESSION_AUDIT_STATE:-$REPO/reports/local-analysis/session-audit}"
PROJECTS="${SESSION_AUDIT_PROJECTS:-$HOME/.claude/projects}"
STARTED_AT="${SESSION_AUDIT_STARTED_AT:?SESSION_AUDIT_STARTED_AT must be the deployment cutoff}"

# 平常只追新增 session：走 free 鏈、同時 10 個（使用者 2026-10-09 決定；free 鏈尾的腿品質較差，見 2026-10-07 同題盲評，
# 準確度由 trial session-audit-precision 追蹤）。開回填批次時改走三條直連池共 40 槽，批次進 review 後下一輪自動退回 free。
# Grok 週額度用到 50% 就讓出（GROK_PAUSE_PERCENT），名額空著不壓到另外兩池。
"$PY" "$DIR/session-audit.py" run \
  --projects "$PROJECTS" \
  --state "$STATE" \
  --started-at "$STARTED_AT" \
  --relay-config "${SESSION_AUDIT_RELAY_CONFIG:-$HOME/.cli-proxy-api/config.yaml}" \
  --keys-file "${SESSION_AUDIT_KEYS_FILE:-$HOME/.cli-proxy-api/keys.env}" \
  --direct-legs "${SESSION_AUDIT_DIRECT_LEGS-}" \
  --backfill-legs "${SESSION_AUDIT_BACKFILL_LEGS-mimo26-pool:15,workbuddy-v41:15,grok-4.7:10}" \
  --max-sessions "${SESSION_AUDIT_MAX_SESSIONS:-10}"
# 分析器的發現量大且未經 review；放在 ~/.claude/friction 外，避免跟其他機制的摩擦混在一起、灌爆 trial-review。
"$PY" "$DIR/session-audit.py" promote \
  --state "$STATE" \
  --friction-root "${SESSION_AUDIT_FRICTION_ROOT:-$STATE/friction}"
