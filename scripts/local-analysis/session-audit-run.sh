#!/bin/bash
{
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
DIR="$(cd "$(dirname "$0")" && pwd -P)"
REPO="$(cd "$DIR/../.." && pwd -P)"
PY="${SESSION_AUDIT_PYTHON:-/opt/homebrew/bin/python3}"
STATE="${SESSION_AUDIT_STATE:-$REPO/reports/local-analysis/session-audit}"
PROJECTS="${SESSION_AUDIT_PROJECTS:-$HOME/.claude/projects}"
STARTED_AT="${SESSION_AUDIT_STARTED_AT:?SESSION_AUDIT_STARTED_AT must be the deployment cutoff}"
SHADOW_STARTED_AT="${SESSION_AUDIT_SHADOW_STARTED_AT:-2026-11-15T00:00:00+08:00}"

# 平常只追新增 session：主線是 Magpie 的 bg-audit-primary（Hydrogen:high，最多同時 2 個）；quality 模式另開獨立副軌 bg-audit-shadow（2 槽）。
# 一回 402／429／503 就冷卻並改走備用 audit-tail（devin-plugin/swe-2:high → vultr/minimax-m3.1-flash → minimax-code-global/MiniMax-M3.1-Flash，以 Magpie group/audit-tail 設定為準，10 槽）；最早一份等待中的新 session 等超過 16 小時也開備用，降到 2 小時以內才關
# （使用者 2026-10-17 決定，經 GPT-6 審查修訂）。開回填批次時改走兩條平行直連線各 5 槽（swe2-high:5,mimo26-pool:5），批次進 review 後下一輪自動退回。
# 回填不走互動 session 共用 Google key 的 Gemini 群組（使用者 2026-10-17 決定）：早期主線用 Argon 時回填曾佔分析器用量 84% 並讓互動端撞 429，
# 且直連線沒有鏈可往下接，429 只能由分析器自己換線。
# --max-sessions 不能高於主線加備用的名額：高出的差額會補成 free 鏈。
# 公司工作目錄目前不另選模型；需要時透過 SESSION_AUDIT_WORKDIR_ROUTES 指定。
"$PY" "$DIR/session-audit.py" run \
  --projects "$PROJECTS" \
  --state "$STATE" \
  --started-at "$STARTED_AT" \
  --shadow-started-at "$SHADOW_STARTED_AT" \
  --relay-config "${SESSION_AUDIT_RELAY_CONFIG:-$HOME/.cli-proxy-api/config.yaml}" \
  --keys-file "${SESSION_AUDIT_KEYS_FILE:-$HOME/.cli-proxy-api/keys.env}" \
  --direct-legs "${SESSION_AUDIT_DIRECT_LEGS-bg-audit-primary:2}" \
  --standby-legs "${SESSION_AUDIT_STANDBY_LEGS-audit-tail:10}" \
  --shadow-legs "${SESSION_AUDIT_SHADOW_LEGS-bg-audit-shadow:2}" \
  --workdir-routes "${SESSION_AUDIT_WORKDIR_ROUTES-}" \
  --backfill-legs "${SESSION_AUDIT_BACKFILL_LEGS-swe2-high:5,mimo26-pool:5}" \
  --max-sessions "${SESSION_AUDIT_MAX_SESSIONS:-2}"
# 2026-10-19 拍板（2026-11-15 升級雙軌）：發現與規則違規改由 session-audit-consume.py 按列級 consumed_at IS NULL 搭雙軌就緒檢查從 state.sqlite 全量分批消費，
# 停止每輪重寫 43.4 MB 的隔離 workflow-general.md（避免無人讀取的死庫存與反覆 I/O）；需舊式匯出時設 SESSION_AUDIT_PROMOTE_FRICTION=1。
if [ "${SESSION_AUDIT_PROMOTE_FRICTION:-0}" = "1" ]; then
  "$PY" "$DIR/session-audit.py" promote \
    --state "$STATE" \
    --friction-root "${SESSION_AUDIT_FRICTION_ROOT:-$STATE/friction}"
fi
exit 0
}
