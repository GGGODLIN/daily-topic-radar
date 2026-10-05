#!/bin/bash
# 只讀已存狀態。不 scan、不 run、不打 provider。
# 分析由 workflow channel 另跑；這裡只呈概況，不另建摩擦嚴重度。
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  printf 'session-audit-daily: execute this file; do not source it\n' >&2
  return 2
fi
set -euo pipefail
cd /
DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd -P)"
PY="${SESSION_AUDIT_PYTHON:-python3}"
SCRIPT="$DIR/session-audit.py"
STATE="${SESSION_AUDIT_STATE:-$REPO/reports/local-analysis/session-audit}"
OUT_DIR="${SESSION_AUDIT_OUT_DIR:-$REPO/reports/local-analysis}"
DATE="${LOCAL_ANALYSIS_DATE:-$(date +%F)}"
OUT="$OUT_DIR/$DATE-session-audit.md"
mkdir -p "$OUT_DIR"
# status 只讀。state 不存在就不建庫、不 scan、不 run、不打模型。
"$PY" - "$SCRIPT" "$STATE" "$OUT" <<'PY'
import json, subprocess, sys
script, state, out = sys.argv[1:]
proc = subprocess.run([sys.executable, script, "status", "--state", state], text=True, capture_output=True, check=False)
if proc.returncode:
    raise SystemExit(proc.returncode)
data = json.loads(proc.stdout)
sources = data.get("sources") or []
def count(status):
    return sum(1 for row in sources if row.get("status") == status)
lines = [
    "只呈概況，不另建摩擦H/M/L",
    "修法不在本報告展開，集中 trial-review。",
    f"sources={len(sources)} included={sum(1 for row in sources if row.get('included'))}",
    f"complete={count('complete')} pending={count('pending')} partial={count('partial')} failed={count('failed')} missing={count('missing')}",
    f"candidates={len(data.get('candidates') or [])}",
]
open(out, "w").write("\n".join(lines) + "\n")
PY
