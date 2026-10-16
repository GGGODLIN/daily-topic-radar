#!/bin/bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
DIR="$(cd "$(dirname "$0")" && pwd -P)"
REPO="$(cd "$DIR/../.." && pwd -P)"
PY="${SESSION_AUDIT_PYTHON:-/opt/homebrew/bin/python3}"
STATE="${SESSION_AUDIT_STATE:-$REPO/reports/local-analysis/session-audit}"
PROJECTS="${SESSION_AUDIT_PROJECTS:-$HOME/.claude/projects}"
STARTED_AT="${SESSION_AUDIT_STARTED_AT:?SESSION_AUDIT_STARTED_AT must be the deployment cutoff}"

# 平常只追新增 session：主線是 Magpie 的 argon-low（Argon low，最多同時 2 個）。Argon 一回 429／503 就冷卻並改走備用
# audit-tail（SWE-2 high → MiMo → DeepSeek V4.1，10 槽）；最早一份等待中的新 session 等超過 16 小時也開備用，降到 2 小時以內才關
# （使用者 2026-10-17 決定，經 GPT-6 審查修訂）。low 與 high 在同日盲評分不出差別、快約 2.6 倍
# （local-llm-gateway .scratch/real-workload-eval/report.md）。開回填批次時改走三條平行直連線各 5 槽，批次進 review 後下一輪自動退回。
# 回填不用 Argon（使用者 2026-10-17 決定）：它跟互動 session 的 Argon 共用同一把 Google key，回填佔了分析器 Argon 用量的 84%，
# 而且直連線沒有鏈可往下接，429 只能由分析器自己換線。
# Grok 週額度用到 50% 就讓出（GROK_PAUSE_PERCENT），名額空著不壓到其他線。
# Argon 跟互動 session 共用同一把 Google key；以前 10 個同時先打 Argon，每分鐘約 24 次，互動端一直撞 429。
# --max-sessions 不能高於主線加備用的名額：高出的差額會補成 free 鏈。
"$PY" "$DIR/session-audit.py" run \
  --projects "$PROJECTS" \
  --state "$STATE" \
  --started-at "$STARTED_AT" \
  --relay-config "${SESSION_AUDIT_RELAY_CONFIG:-$HOME/.cli-proxy-api/config.yaml}" \
  --keys-file "${SESSION_AUDIT_KEYS_FILE:-$HOME/.cli-proxy-api/keys.env}" \
  --direct-legs "${SESSION_AUDIT_DIRECT_LEGS-argon-low:2}" \
  --standby-legs "${SESSION_AUDIT_STANDBY_LEGS-audit-tail:10}" \
  --backfill-legs "${SESSION_AUDIT_BACKFILL_LEGS-swe2-high:5,mimo26-pool:5,grok-4.7:5}" \
  --max-sessions "${SESSION_AUDIT_MAX_SESSIONS:-2}"
# 分析器的發現量大且未經 review；放在 ~/.claude/friction 外，避免跟其他機制的摩擦混在一起、灌爆 trial-review。
"$PY" "$DIR/session-audit.py" promote \
  --state "$STATE" \
  --friction-root "${SESSION_AUDIT_FRICTION_ROOT:-$STATE/friction}"
