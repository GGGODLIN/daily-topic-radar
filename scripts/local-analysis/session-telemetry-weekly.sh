#!/bin/bash
# session-telemetry-weekly.sh — /daily-local workflow 的 session-telemetry channel。
#
# 無狀態、零 LLM、零常駐 DB：直接流式掃描 projects 目錄產出每週遙測健康度報告。

set -euo pipefail
S="$(cd "$(dirname "$0")" && pwd)"

DATE="${LOCAL_ANALYSIS_DATE:-$(date +%Y-%m-%d)}"
OUT_DIR="${SESSION_TELEMETRY_OUT_DIR:-/Users/linhancheng/code/social-info/reports/local-analysis}"
OUT="$OUT_DIR/$DATE-session-telemetry.md"
mkdir -p "$OUT_DIR"

EXTRA_ARGS=()
if [ -n "${SESSION_TELEMETRY_PROJECTS_DIR:-}" ]; then
  EXTRA_ARGS+=(--projects-dir "$SESSION_TELEMETRY_PROJECTS_DIR")
fi

python3 "$S/session-telemetry.py" --date "$DATE" --format markdown ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} > "$OUT"
