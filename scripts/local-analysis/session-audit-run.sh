#!/bin/bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
DIR="$(cd "$(dirname "$0")" && pwd -P)"
REPO="$(cd "$DIR/../.." && pwd -P)"
PY="${SESSION_AUDIT_PYTHON:-/opt/homebrew/bin/python3}"
STATE="${SESSION_AUDIT_STATE:-$REPO/reports/local-analysis/session-audit}"
PROJECTS="${SESSION_AUDIT_PROJECTS:-$HOME/.claude/projects}"
STARTED_AT="${SESSION_AUDIT_STARTED_AT:?SESSION_AUDIT_STARTED_AT must be the deployment cutoff}"

# 舊紀錄慢慢回填，但上線後的新／續跑來源不能隨日子過去降成歷史。
"$PY" "$DIR/session-audit.py" run \
  --projects "$PROJECTS" \
  --state "$STATE" \
  --started-at "$STARTED_AT" \
  --relay-config "${SESSION_AUDIT_RELAY_CONFIG:-$HOME/.cli-proxy-api/config.yaml}" \
  --keys-file "${SESSION_AUDIT_KEYS_FILE:-$HOME/.cli-proxy-api/keys.env}"
# 分析器的發現量大且未經 review；放在 ~/.claude/friction 外，避免跟其他機制的摩擦混在一起、灌爆 trial-review。
"$PY" "$DIR/session-audit.py" promote \
  --state "$STATE" \
  --friction-root "${SESSION_AUDIT_FRICTION_ROOT:-$STATE/friction}"
