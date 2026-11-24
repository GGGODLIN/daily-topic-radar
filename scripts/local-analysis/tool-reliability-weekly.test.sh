#!/bin/bash
# tool-reliability-weekly.sh 離線回歸測試（全數寫入 mktemp 隔離目錄，嚴禁觸碰正式 reports/）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WRAPPER="$SCRIPT_DIR/tool-reliability-weekly.sh"

T="$(mktemp -d)"
trap 'rc=$?; rm -rf "$T"; exit $rc' EXIT

pass=0; fail=0
check() {
  local desc="$1"; shift
  if "$@"; then pass=$((pass+1)); echo "  ok   $desc"; else fail=$((fail+1)); echo "  FAIL $desc"; fi
}

HOME_DIR="$T/fake-home"
OUT_DIR="$T/reports"
PROJ_DIR="$HOME_DIR/.claude/projects/proj-a"
mkdir -p "$PROJ_DIR"

TS="$(python3 -c 'import datetime; print(datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))')"
python3 - "$PROJ_DIR/s1.jsonl" "$TS" <<'PY'
import json, sys
path, ts = sys.argv[1], sys.argv[2]
lines = []
for i in range(22):
    lines.append(json.dumps({"type": "assistant", "timestamp": ts, "message": {"content": [{"type": "tool_use", "id": f"t{i}", "name": "Bash"}]}}))
    lines.append(json.dumps({"type": "user", "timestamp": ts, "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}", "is_error": i < 4}]}}))
with open(path, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
PY

# 1. 正常端對端執行：寫入隔離 OUT_DIR 且自動建目錄
HOME="$HOME_DIR" LOCAL_ANALYSIS_DATE="2026-11-24" TOOL_RELIABILITY_OUT_DIR="$OUT_DIR" \
  bash "$WRAPPER" >"$T/run1.out" 2>"$T/run1.err"
OUT_MD="$OUT_DIR/2026-11-24-tool-reliability.md"

check "正常執行產出目標 Markdown 且建立 OUT_DIR" test -f "$OUT_MD"
check "報告包含工具可靠度標題與 Bash 統計" bash -c "grep -q '^## 工具可靠度：' '$OUT_MD' && grep -q '| Bash | 22 | 4 |' '$OUT_MD'"
check "正常完成後無殘留暫存檔" bash -c "[ -z \"\$(find '$OUT_DIR' -name '.tool-reliability.*')\" ]"

# 2. 冪等重跑：內容完全一致
cp "$OUT_MD" "$T/run1.md"
HOME="$HOME_DIR" LOCAL_ANALYSIS_DATE="2026-11-24" TOOL_RELIABILITY_OUT_DIR="$OUT_DIR" \
  bash "$WRAPPER" >"$T/run2.out" 2>"$T/run2.err"
check "重跑冪等且報告內容一致" cmp -s "$T/run1.md" "$OUT_MD"

# 3. 抽取階段失敗（缺 projects 目錄）時：不可吞掉非零退出碼，也不可覆寫或輸出舊報
STALE_HOME="$T/stale-home"
STALE_DATA="$STALE_HOME/code/social-info/reports/local-analysis/tool-reliability.jsonl"
mkdir -p "$(dirname "$STALE_DATA")"
printf '{"week":"2026-W47","tool":"StaleTool","calls":25,"errors":5,"rate":0.2}\n' > "$STALE_DATA"
SENTINEL_OUT="$T/sentinel/2026-11-24-tool-reliability.md"
mkdir -p "$(dirname "$SENTINEL_OUT")"
printf 'ORIGINAL-SENTINEL\n' > "$SENTINEL_OUT"

rc=0
HOME="$STALE_HOME" LOCAL_ANALYSIS_DATE="2026-11-24" TOOL_RELIABILITY_OUT="$SENTINEL_OUT" \
  bash "$WRAPPER" >"$T/fail1.out" 2>"$T/fail1.err" || rc=$?
check "抽取失敗回傳非零退出碼" test "$rc" -ne 0
check "抽取失敗錯誤訊息保留在 stderr" test -s "$T/fail1.err"
check "抽取失敗不覆寫既有報告也不誤出舊報" grep -qx 'ORIGINAL-SENTINEL' "$SENTINEL_OUT"

# 4. 抽取成功但無任何有效資料（--report 失敗）時：回傳非零且不截斷既有報告、不留暫存檔
EMPTY_HOME="$T/empty-home"
mkdir -p "$EMPTY_HOME/.claude/projects/empty-proj"
SENTINEL_OUT2="$T/sentinel2/2026-11-24-tool-reliability.md"
mkdir -p "$(dirname "$SENTINEL_OUT2")"
printf 'KEEP-ME-INTACT\n' > "$SENTINEL_OUT2"

rc2=0
HOME="$EMPTY_HOME" LOCAL_ANALYSIS_DATE="2026-11-24" TOOL_RELIABILITY_OUT="$SENTINEL_OUT2" \
  bash "$WRAPPER" >"$T/fail2.out" 2>"$T/fail2.err" || rc2=$?
check "無資料 --report 失敗回傳非零退出碼" test "$rc2" -ne 0
check "--report 失敗不截斷既有輸出檔" grep -qx 'KEEP-ME-INTACT' "$SENTINEL_OUT2"
check "--report 失敗後自動清理暫存檔" bash -c "[ -z \"\$(find '$(dirname "$SENTINEL_OUT2")' -name '.tool-reliability.*')\" ]"

# 5. TOOL_RELIABILITY_OUT 覆寫到尚未建立的多層目錄
CUSTOM_OUT="$T/nested/dir/custom-report.md"
HOME="$HOME_DIR" LOCAL_ANALYSIS_DATE="2026-11-24" TOOL_RELIABILITY_OUT="$CUSTOM_OUT" \
  bash "$WRAPPER"
check "TOOL_RELIABILITY_OUT 自動建立父目錄並寫出" grep -q '| Bash | 22 | 4 |' "$CUSTOM_OUT"

echo "$pass/$((pass+fail)) passed"
[ "$fail" -eq 0 ]
