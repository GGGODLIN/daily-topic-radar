#!/usr/bin/env bash
# free-pool-daily.sh 的行為測試：ports、hello 腿與 relay-watch report 全用 fixture，
# 不碰真實 relay、不送真實請求。relay-watch 以 stub 代替，它的報告內容由
# cliproxyapi-setup 的 tools/relay-watch/report.test.py 負責。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WRAPPER="$SCRIPT_DIR/free-pool-daily.sh"
RUNNER="$SCRIPT_DIR/run-shell-channel.sh"
TMP="$(mktemp -d)"
SERVER_PID=""
PASS=0
FAIL=0
DATE="2026-09-28"

cleanup() {
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$1"; }

assert_contains() {
  if printf '%s' "$3" | grep -qF -- "$2"; then pass "$1"; else fail "$1 — missing: $2"; fi
}
assert_not_contains() {
  if printf '%s' "$3" | grep -qF -- "$2"; then fail "$1 — unexpected: $2"; else pass "$1"; fi
}

cat > "$TMP/server.py" <<'PY'
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

mode_path = Path(os.environ["MODE_PATH"])
port_path = Path(os.environ["PORT_PATH"])

class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        return

    def reply(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply(200, {"status": "ok"})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if mode_path.read_text().strip() == "hello-402":
            self.reply(402, {"error": {"message": "insufficient credits"}})
        else:
            self.reply(200, {"choices": [{"message": {"content": "hi"}}],
                             "usage": {"completion_tokens": 1}})

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
port_path.write_text(str(server.server_address[1]))
server.serve_forever()
PY

echo ok > "$TMP/mode"
MODE_PATH="$TMP/mode" PORT_PATH="$TMP/port" python3 "$TMP/server.py" &
SERVER_PID=$!
for _ in $(seq 1 50); do [ -s "$TMP/port" ] && break; sleep 0.1; done
PORT="$(cat "$TMP/port")"

cat > "$TMP/config.yaml" <<YAML
openai-compatibility:
  - name: fake-leg
    base-url: http://127.0.0.1:$PORT/v1
    api-key-entries:
      - api-key: sk-fixture-leg
    models:
      - name: fake-model
        alias: free
YAML
cat > "$TMP/cline.json" <<'JSON'
{"accounts": [{"status": "active", "modelCooldowns": {}}]}
JSON

# stub relay-watch：STUB_MODE 決定報告內容或失敗。
cat > "$TMP/relay_watch_stub.py" <<'PY'
import os
import sys

assert sys.argv[1:] == ["report"], sys.argv
mode = os.environ["STUB_MODE"]
if mode == "fail":
    print("Traceback: fixture crash", file=sys.stderr)
    sys.exit(1)
print("## 過去 24 小時")
print()
if mode == "clean":
    print("過去 24 小時無異常")
    print("自檢：資料讀取正常｜收集器健康未偵測｜最近 1 小時 12 個請求，最後一筆 11:48")
else:
    print("結論：⚠ 1 項異常")
    print()
    print("### 達到門檻的 alias")
    print("- ⚠ 警告｜groq-qwen：24 小時失敗率 45%（90/200）")
PY

run_wrapper() {
  local stub_mode="$1" hello_mode="$2" rw="${3:-$TMP/relay_watch_stub.py}"
  echo "$hello_mode" > "$TMP/mode"
  rm -rf "$TMP/out" "$TMP/logs"
  STUB_MODE="$stub_mode" \
  LOCAL_ANALYSIS_DATE="$DATE" \
  FREE_POOL_OUT_DIR="$TMP/out" \
  FREE_POOL_LOG_DIR_PATH="$TMP/logs" \
  FREE_POOL_RELAY_CONFIG="$TMP/config.yaml" \
  FREE_POOL_CLINE_ACCOUNTS="$TMP/cline.json" \
  FREE_POOL_PORT_RELAY="$PORT" \
  FREE_POOL_PORT_LITELLM="$PORT" \
  FREE_POOL_PORT_CLINE="$PORT" \
  FREE_POOL_AR_URL="http://127.0.0.1:$PORT" \
  FREE_POOL_MIMO_HEALTH="http://127.0.0.1:$PORT/health" \
  FREE_POOL_RELAY_WATCH="$rw" \
  bash "${RUN_WITH:-$WRAPPER}" ${RUN_ARGS:-} >/dev/null 2>&1
}

out_file="$TMP/out/$DATE-free-pool.md"
log_file="$TMP/logs/local-analysis-free-pool-$DATE.log"

# 1. hello 全綠＋24 小時無異常：維持 __SILENT__，證據留在 LOG。
run_wrapper clean ok; rc=$?
[ "$rc" -eq 0 ] && pass "all green exits 0" || fail "all green exits 0 — rc=$rc"
[ "$(cat "$out_file" 2>/dev/null)" = "__SILENT__" ] && pass "all green stays __SILENT__" || fail "all green stays __SILENT__ — $(cat "$out_file" 2>/dev/null)"
assert_contains "all green logs the 24h section" "過去 24 小時無異常" "$(cat "$log_file" 2>/dev/null)"

# 2. hello 全綠但 24 小時有異常：報告要出，hello 結論是「小請求存活」。
run_wrapper anomaly ok; rc=$?
out="$(cat "$out_file" 2>/dev/null)"
[ "$rc" -eq 0 ] && pass "24h anomaly exits 0" || fail "24h anomaly exits 0 — rc=$rc"
assert_not_contains "24h anomaly is not silent" "__SILENT__" "$out"
assert_contains "hello conclusion says 小請求存活" "結論：小請求存活" "$out"
assert_contains "24h section appended" "## 過去 24 小時" "$out"
assert_contains "24h anomaly lines kept" "### 達到門檻的 alias" "$out"

# 3. hello 有發現＋24 小時無異常：原有段落不變，後面接「過去 24 小時」段。
run_wrapper clean hello-402; rc=$?
out="$(cat "$out_file" 2>/dev/null)"
[ "$rc" -eq 0 ] && pass "hello finding exits 0" || fail "hello finding exits 0 — rc=$rc"
assert_contains "hello report title unchanged" "# free-pool liveness $DATE" "$out"
assert_contains "hello conclusion line unchanged" "結論：⚠ 1 個發現（嚴重度排序：全池級→花錢來源→池內其他→探測失敗）" "$out"
assert_contains "hello finding bullet unchanged" "- ⚠ [fake-leg] hello 失敗：額度耗盡" "$out"
assert_not_contains "hello finding has no 小請求存活" "小請求存活" "$out"
assert_contains "no-anomaly line in report" "過去 24 小時無異常" "$out"
assert_contains "self-check in report" "自檢：資料讀取正常" "$out"

# 4. relay-watch 失敗：wrapper 非零，經 runner 後沒有報告（workflow 記 failed → digest「偵測 channel 失敗」）。
run_wrapper fail ok; rc=$?
[ "$rc" -ne 0 ] && pass "relay-watch crash fails the wrapper" || fail "relay-watch crash fails the wrapper — rc=$rc"
assert_contains "crash reason in LOG" "relay-watch report 失敗" "$(cat "$log_file" 2>/dev/null)"
run_wrapper clean ok "$TMP/does-not-exist.py"; rc=$?
[ "$rc" -ne 0 ] && pass "missing relay-watch fails the wrapper" || fail "missing relay-watch fails the wrapper — rc=$rc"
RUN_WITH="$RUNNER" RUN_ARGS="$WRAPPER $out_file $DATE true" run_wrapper fail ok; rc=$?
[ "$rc" -ne 0 ] && pass "runner reports channel failure" || fail "runner reports channel failure — rc=$rc"
[ ! -e "$out_file" ] && pass "runner leaves no report on failure" || fail "runner leaves no report on failure"

bash -n "$WRAPPER" && pass "wrapper syntax" || fail "wrapper syntax"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
