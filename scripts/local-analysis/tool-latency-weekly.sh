#!/bin/bash
# tool-latency-weekly.sh — /daily-local workflow 的 tool-latency channel（2026-11-10 建）。
#
# kind: 'shell'——刻意零 LLM，只做確定性統計；沒有變慢項時報告是 __SILENT__，不進 digest。
# 設計取捨與 tool-reliability 的關係寫在 tool-latency-extract.py 檔頭。

set -uo pipefail
S="$(cd "$(dirname "$0")" && pwd)"

DATE="${LOCAL_ANALYSIS_DATE:-$(date +%Y-%m-%d)}"
OUT_DIR="${TOOL_LATENCY_OUT_DIR:-/Users/linhancheng/code/social-info/reports/local-analysis}"
OUT="$OUT_DIR/$DATE-tool-latency.md"
mkdir -p "$OUT_DIR"

python3 "$S/tool-latency-extract.py" --report --date "$DATE" > "$OUT"
