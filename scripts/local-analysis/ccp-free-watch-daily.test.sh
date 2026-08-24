#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WRAPPER="$SCRIPT_DIR/ccp-free-watch-daily.sh"
TMP="$(mktemp -d)"
SERVER_PID=""
PASS=0
FAIL=0

cleanup() {
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

pass() {
  PASS=$((PASS + 1))
  printf 'PASS: %s\n' "$1"
}

fail() {
  FAIL=$((FAIL + 1))
  local message="${1//fake-openrouter-secret/[REDACTED]}"
  printf 'FAIL: %s\n' "$message"
}

assert_eq() {
  local name="$1"
  local expected="$2"
  local actual="$3"
  if [ "$actual" = "$expected" ]; then
    pass "$name"
  else
    fail "$name — expected=$expected actual=$actual"
  fi
}

cat > "$TMP/server.py" <<'PY'
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

scenario_path = Path(os.environ["SCENARIO_PATH"])
port_path = Path(os.environ["PORT_PATH"])
calls_path = Path(os.environ["CALLS_PATH"])

class Handler(BaseHTTPRequestHandler):
  def log_message(self, _format, *_args):
    return

  def scenario(self):
    return json.loads(scenario_path.read_text())

  def send_json(self, status, value):
    body = json.dumps(value).encode()
    self.send_response(status)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

  def do_GET(self):
    scenario = self.scenario()
    if self.path == "/api/v1/models":
      if scenario.get("models_redirect"):
        self.send_response(302)
        self.send_header("Location", "/redirect-target")
        self.end_headers()
        return
      status = scenario.get("models_status", 200)
      if status == 200:
        self.send_json(200, scenario.get("models_payload", {"data": scenario["models"]}))
      else:
        body = json.dumps({"error": scenario.get("models_error", "temporary")}).encode()
        self.send_response(status, scenario.get("models_reason"))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
      return
    if self.path == "/api/v1/key":
      self.send_json(200, scenario.get("key_payload", {"data": scenario["key"]}))
      return
    if self.path == "/redirect-target":
      with calls_path.open("a") as handle:
        handle.write(json.dumps({
          "path": self.path,
          "authorization_present": bool(self.headers.get("Authorization")),
        }) + "\n")
      self.send_json(200, {"data": scenario["models"]})
      return
    if self.path == "/terms/stealth":
      body = scenario.get("terms", "terms-v1").encode()
      self.send_response(200)
      self.send_header("Content-Length", str(len(body)))
      self.end_headers()
      self.wfile.write(body)
      return
    prefix = "/api/v1/models/"
    suffix = "/endpoints"
    if self.path.startswith(prefix) and self.path.endswith(suffix):
      model = self.path[len(prefix):-len(suffix)]
      payloads = scenario.get("endpoint_payloads", {})
      self.send_json(200, payloads.get(model, {"data": {"endpoints": scenario["endpoints"].get(model, [])}}))
      return
    self.send_json(404, {"error": "not found"})

  def do_POST(self):
    scenario = self.scenario()
    length = int(self.headers.get("Content-Length", "0"))
    payload = json.loads(self.rfile.read(length) or b"{}")
    with calls_path.open("a") as handle:
      handle.write(json.dumps({
        "path": self.path,
        "authorization_present": bool(self.headers.get("Authorization")),
        "model": payload.get("model"),
        "messages": payload.get("messages"),
        "tool_choice": payload.get("tool_choice"),
        "provider": payload.get("provider"),
        "max_tokens": payload.get("max_tokens"),
      }) + "\n")
    if self.path != "/api/v1/chat/completions":
      self.send_json(404, {"error": "not found"})
      return
    if scenario.get("chat_redirect"):
      self.send_response(302)
      self.send_header("Location", "/redirect-target")
      self.end_headers()
      return
    if not scenario.get("gate_success", True):
      self.send_json(200, {"choices": [{"message": {"content": "wrong"}}]})
      return
    if payload.get("tool_choice"):
      arguments = {"marker": "CCP_FREE_WATCH_TOOL_OK"}
      if scenario.get("gate_extra_argument"):
        arguments["extra"] = "unexpected"
      calls = [{"function": {"name": "ccp_free_watch_marker", "arguments": json.dumps(arguments)}}]
      if scenario.get("gate_extra_tool"):
        calls.append({"function": {"name": "other_tool", "arguments": "{}"}})
      self.send_json(200, {"choices": [{"message": {"tool_calls": calls}}]})
      return
    self.send_json(200, {"choices": [{"message": {"content": "CCP_FREE_WATCH_PROMPT_OK"}}]})

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
port_path.write_text(str(server.server_port))
server.serve_forever()
PY

cat > "$TMP/scenario.json" <<'JSON'
{
  "models": [
    {
      "id": "stealth/ox-alpha",
      "name": "Ox Alpha",
      "description": "Coding and sustained agentic work",
      "pricing": {"prompt": "0", "completion": "0"},
      "context_length": 1048576,
      "supported_parameters": ["tools", "tool_choice", "reasoning"],
      "expiration_date": "2098-12-31",
      "links": {"details": "/api/v1/models/stealth/ox-alpha/endpoints"}
    }
  ],
  "endpoints": {
    "stealth/ox-alpha": [
      {"provider_name": "Stealth", "status": 0, "pricing": {"prompt": "0", "completion": "0"}}
    ]
  },
  "key": {
    "expires_at": null,
    "limit": 0.01,
    "limit_remaining": 0.01,
    "is_free_tier": true
  },
  "terms": "terms-v1"
}
JSON

SCENARIO_PATH="$TMP/scenario.json" PORT_PATH="$TMP/port" CALLS_PATH="$TMP/calls.jsonl" python3 "$TMP/server.py" > "$TMP/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 50); do
  [ -s "$TMP/port" ] && break
  sleep 0.05
done
PORT="$(cat "$TMP/port")"
BASE_URL="http://127.0.0.1:$PORT"
cp "$TMP/scenario.json" "$TMP/scenario.base.json"

set_scenario() {
  cp "$TMP/scenario.base.json" "$TMP/scenario.json"
  python3 - "$TMP/scenario.json" "$1" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
case = sys.argv[2]
data = json.loads(path.read_text())
if case == "missing-model":
  data["models"] = []
elif case == "paid-model":
  data["models"][0]["pricing"]["completion"] = "0.000001"
elif case == "paid-model-numeric":
  data["models"][0]["pricing"]["completion"] = 0.000001
elif case == "active-extra-fee":
  data["models"][0]["pricing"]["request"] = "0.04"
elif case == "active-missing-tools":
  data["models"][0]["supported_parameters"] = ["tools", "reasoning"]
elif case == "active-low-context":
  data["models"][0]["context_length"] = 100000
elif case == "active-expiring":
  data["models"][0]["expiration_date"] = "2026-08-25"
elif case == "empty-endpoints":
  data["endpoints"]["stealth/ox-alpha"] = []
elif case == "active-unhealthy-endpoint":
  data["endpoints"]["stealth/ox-alpha"][0]["status"] = -1
elif case == "active-paid-endpoint":
  data["endpoints"]["stealth/ox-alpha"][0]["pricing"]["request"] = "0.04"
elif case == "expiring-key":
  data["key"]["expires_at"] = "2026-08-25T00:00:00Z"
elif case in {"candidate", "candidate-gate-fail", "candidate-extra-fee", "candidate-extra-tool", "candidate-extra-argument", "candidate-terms-change", "candidate-malformed-alongside-valid", "candidate-chat-redirect"}:
  candidate = {
    "id": "stealth/new-alpha",
    "name": "New Alpha",
    "description": "Coding and long-horizon agentic software engineering",
    "pricing": {"prompt": "0", "completion": "0", "request": "0"},
    "context_length": 500000,
    "supported_parameters": ["tools", "tool_choice", "reasoning"],
    "expiration_date": "2098-12-31"
  }
  if case == "candidate-extra-fee":
    candidate["pricing"]["request"] = "0.04"
  if case == "candidate-malformed-alongside-valid":
    data["models"].append({
      "id": "stealth/malformed-alpha",
      "name": "Malformed Alpha",
      "description": "Coding and agentic software engineering",
      "pricing": {"prompt": "0", "completion": "0"},
      "context_length": "unknown",
      "supported_parameters": ["tools", "tool_choice"],
      "expiration_date": "not-a-date"
    })
  data["models"].append(candidate)
  data["endpoints"]["stealth/new-alpha"] = [
    {"provider_name": "Stealth", "status": 0, "pricing": {"prompt": "0", "completion": "0"}}
  ]
  data["gate_success"] = case != "candidate-gate-fail"
  data["gate_extra_tool"] = case == "candidate-extra-tool"
  data["gate_extra_argument"] = case == "candidate-extra-argument"
  data["chat_redirect"] = case == "candidate-chat-redirect"
  if case == "candidate-terms-change":
    data["terms"] = "terms-v2"
elif case == "models-429":
  data["models_status"] = 429
elif case == "models-redirect":
  data["models_redirect"] = True
elif case == "models-malformed":
  data["models_payload"] = {"error": "temporary"}
elif case == "key-malformed":
  data["key_payload"] = {"error": "temporary"}
elif case == "active-endpoints-malformed":
  data["endpoint_payloads"] = {"stealth/ox-alpha": {"error": "temporary"}}
elif case == "candidate-missing-status":
  candidate = {
    "id": "stealth/statusless-alpha",
    "name": "Statusless Alpha",
    "description": "Coding and agentic software engineering",
    "pricing": {"prompt": "0", "completion": "0"},
    "context_length": 500000,
    "supported_parameters": ["tools", "tool_choice"],
    "expiration_date": "2098-12-31"
  }
  data["models"].append(candidate)
  data["endpoints"][candidate["id"]] = [
    {"provider_name": "Stealth", "pricing": {"prompt": "0", "completion": "0"}}
  ]
elif case == "models-secret-error":
  data["models_status"] = 500
  data["models_reason"] = "fake-openrouter-secret"
  data["models_error"] = "fake-openrouter-secret"
elif case == "candidate-secret-id":
  candidate = {
    "id": "stealth/fake-openrouter-secret-alpha",
    "name": "Secret Alpha",
    "description": "Coding and agentic software engineering",
    "pricing": {"prompt": "0", "completion": "0"},
    "context_length": 500000,
    "supported_parameters": ["tools", "tool_choice"],
    "expiration_date": "2098-12-31"
  }
  data["models"].append(candidate)
  data["endpoints"][candidate["id"]] = [
    {"provider_name": "Stealth", "status": 0, "pricing": {"prompt": "0", "completion": "0"}}
  ]
elif case == "terms-change":
  data["terms"] = "terms-v2"
path.write_text(json.dumps(data))
PY
}

cat > "$TMP/runtime.env" <<'ENV'
OPENROUTER_API_KEY=fake-openrouter-secret
ENV
chmod 600 "$TMP/runtime.env"
cat > "$TMP/install-metadata.json" <<JSON
{
  "model_ref": "open_router/stealth/ox-alpha",
  "endpoint": "http://127.0.0.1:18082",
  "secret_env": "$TMP/runtime.env"
}
JSON

run_wrapper() {
  CCP_FREE_WATCH_METADATA="$TMP/install-metadata.json" \
  CCP_FREE_WATCH_BASELINE="$TMP/baseline.json" \
  CCP_FREE_WATCH_OUT="${CCP_FREE_WATCH_OUT_OVERRIDE:-$TMP/report.md}" \
  CCP_FREE_WATCH_LOG="$TMP/watch.log" \
  CCP_FREE_WATCH_MODELS_URL="$BASE_URL/api/v1/models" \
  CCP_FREE_WATCH_KEY_URL="$BASE_URL/api/v1/key" \
  CCP_FREE_WATCH_API_ROOT="$BASE_URL/api/v1" \
  CCP_FREE_WATCH_CHAT_URL="$BASE_URL/api/v1/chat/completions" \
  CCP_FREE_WATCH_TERMS_URL="$BASE_URL/terms/stealth" \
  CCP_FREE_WATCH_NOW="${CCP_FREE_WATCH_NOW_OVERRIDE:-2026-08-24T00:00:00Z}" \
  CCP_FREE_WATCH_MIN_CONTEXT="${CCP_FREE_WATCH_MIN_CONTEXT_OVERRIDE:-200000}" \
  LOCAL_ANALYSIS_DATE="2026-08-24" \
  bash "$WRAPPER" > "$TMP/stdout" 2> "$TMP/stderr"
}

assert_current_outputs_redacted() {
  local hits=0
  for path in "$TMP/report.md" "$TMP/baseline.json" "$TMP/stdout" "$TMP/stderr" "$TMP/watch.log"; do
    if [ -f "$path" ] && grep -Fq 'fake-openrouter-secret' "$path"; then
      hits=$((hits + 1))
    fi
  done
  if [ "$hits" -eq 0 ]; then
    pass "本輪產物沒有 provider key"
  else
    fail "本輪產物洩漏 provider key"
  fi
}

run_wrapper_expect_success() {
  if run_wrapper; then
    pass "reportable scenario exit 0"
  else
    fail "reportable scenario 應 exit 0"
  fi
  assert_current_outputs_redacted
}

if run_wrapper; then
  pass "健康 route wrapper 成功退出"
else
  fail "健康 route wrapper 應成功退出"
fi
assert_eq "首次健康狀態保持 silent" "__SILENT__" "$(cat "$TMP/report.md" 2>/dev/null || true)"
assert_eq "建立非敏感 baseline" "true" "$([ -s "$TMP/baseline.json" ] && printf true || printf false)"
assert_current_outputs_redacted

set_scenario "missing-model"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
if run_wrapper; then
  pass "active model 消失時 wrapper 仍成功交付 finding"
else
  fail "active model 消失時 wrapper 不應破壞 shell channel"
fi
if grep -q '^# ccp-free 需要處理' "$TMP/report.md" && grep -q 'active model 已從 OpenRouter catalog 消失' "$TMP/report.md"; then
  pass "active model 消失產生決策 finding"
else
  fail "active model 消失缺少決策 finding"
fi
rm -f "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
run_wrapper_expect_success
assert_eq "相同 hard state 只通知一次" "__SILENT__" "$(cat "$TMP/report.md" 2>/dev/null || true)"

set_scenario "missing-model"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
mkdir -p "$TMP/report-target-directory"
if CCP_FREE_WATCH_OUT_OVERRIDE="$TMP/report-target-directory" run_wrapper; then
  fail "report 寫入失敗不應冒充成功"
else
  pass "report 寫入失敗會使 wrapper 非零"
fi
assert_eq "report 失敗前不提交 baseline" "false" "$([ -e "$TMP/baseline.json" ] && printf true || printf false)"
rm -f "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
if run_wrapper && grep -q 'Active route 已退場' "$TMP/report.md"; then
  pass "report 恢復後仍重送原 finding"
else
  fail "report 失敗造成 finding 永久沉默"
fi

set_scenario "paid-model"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
run_wrapper_expect_success
if grep -q '^# ccp-free 需要處理' "$TMP/report.md" && grep -q '價格不再是 0/0' "$TMP/report.md"; then
  pass "active model 改價產生決策 finding"
else
  fail "active model 改價缺少決策 finding"
fi
set_scenario "paid-model-numeric"
rm -f "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
run_wrapper_expect_success
assert_eq "等價 Decimal 表示不重複產生 paid finding" "__SILENT__" "$(cat "$TMP/report.md" 2>/dev/null || true)"

set_scenario "active-extra-fee"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
run_wrapper_expect_success
if grep -q '^# ccp-free 需要處理' "$TMP/report.md" && grep -q '價格不再是 0/0' "$TMP/report.md"; then
  pass "active model 額外費用不會 fail-open"
else
  fail "active model 額外費用被誤判為免費"
fi

for scenario in active-missing-tools active-low-context active-expiring; do
  set_scenario "$scenario"
  rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
  run_wrapper_expect_success
  if grep -q '^# ccp-free 需要處理' "$TMP/report.md" && grep -q 'Active model metadata Gate 失敗' "$TMP/report.md"; then
    pass "$scenario 產生 metadata Gate finding"
  else
    fail "$scenario 被誤判為健康"
  fi
done

set_scenario "empty-endpoints"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
run_wrapper_expect_success
if grep -q '^# ccp-free 需要處理' "$TMP/report.md" && grep -q '沒有可用 endpoint' "$TMP/report.md"; then
  pass "active model endpoint 為空產生決策 finding"
else
  fail "active model endpoint 為空缺少決策 finding"
fi

for scenario in active-unhealthy-endpoint active-paid-endpoint; do
  set_scenario "$scenario"
  rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
  run_wrapper_expect_success
  if grep -q '^# ccp-free 需要處理' "$TMP/report.md" && grep -q '沒有健康且零價 endpoint' "$TMP/report.md"; then
    pass "$scenario 產生 endpoint Gate finding"
  else
    fail "$scenario 被誤判為可用"
  fi
done

set_scenario "expiring-key"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
run_wrapper_expect_success
if grep -q '^# ccp-free 需要處理' "$TMP/report.md" && grep -q 'API key 將於 1 天內到期' "$TMP/report.md"; then
  pass "credential 接近到期產生決策 finding"
else
  fail "credential 接近到期缺少決策 finding"
fi

set_scenario "candidate"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr" "$TMP/calls.jsonl"
run_wrapper_expect_success
if grep -q '^# ccp-free 新候選' "$TMP/report.md" && grep -q 'stealth/new-alpha' "$TMP/report.md"; then
  pass "通過 synthetic Gate 的新 preview 產生 finding"
else
  fail "合格新 preview 缺少 finding"
fi
GATE_CALLS="$([ -f "$TMP/calls.jsonl" ] && wc -l < "$TMP/calls.jsonl" | tr -d ' ' || printf 0)"
assert_eq "新候選執行 prompt 與 forced tool 兩個 Gate" "2" "$GATE_CALLS"
if python3 - "$TMP/calls.jsonl" <<'PY'
import json
import sys
rows = [json.loads(line) for line in open(sys.argv[1])]
assert all(row["authorization_present"] for row in rows)
assert all(row["model"] == "stealth/new-alpha" for row in rows)
assert rows[0]["messages"] == [{"role": "user", "content": "Reply with exactly CCP_FREE_WATCH_PROMPT_OK"}]
assert rows[1]["tool_choice"] == {"type": "function", "function": {"name": "ccp_free_watch_marker"}}
expected_provider = {"allow_fallbacks": False, "require_parameters": True, "max_price": {"prompt": "0", "completion": "0"}}
assert all(row["provider"] == expected_provider for row in rows)
assert all(row["max_tokens"] == 1024 for row in rows)
PY
then
  pass "synthetic Gate 內容固定且帶 auth"
else
  fail "synthetic Gate request 不符合固定契約"
fi

rm -f "$TMP/report.md" "$TMP/stdout" "$TMP/stderr" "$TMP/calls.jsonl"
run_wrapper_expect_success
assert_eq "已見候選不重跑 Gate 且保持 silent" "__SILENT__" "$(cat "$TMP/report.md" 2>/dev/null || true)"
assert_eq "已見候選沒有重送 inference" "0" "$([ -f "$TMP/calls.jsonl" ] && wc -l < "$TMP/calls.jsonl" | tr -d ' ' || printf 0)"

set_scenario "missing-model"
rm -f "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
run_wrapper_expect_success
if python3 - "$TMP/baseline.json" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1]))
assert data.get("terms_sha256")
assert "stealth/new-alpha" in data.get("candidate_ids", [])
assert data.get("issue") == "active-model-missing"
PY
then
  pass "hard state 保留 terms 與 candidate baseline"
else
  fail "hard state 覆蓋完整 baseline"
fi

set_scenario "candidate-gate-fail"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr" "$TMP/calls.jsonl"
run_wrapper_expect_success
if grep -q '^# ccp-free 候選檢查' "$TMP/report.md" && grep -q '未通過 Gate' "$TMP/report.md" && ! grep -q '建議：決定是否開 Harbor trial' "$TMP/report.md"; then
  pass "Gate 失敗只留診斷、不建議切換"
else
  fail "Gate 失敗的 report 語意錯誤"
fi
rm -f "$TMP/report.md" "$TMP/stdout" "$TMP/stderr" "$TMP/calls.jsonl"
run_wrapper_expect_success
assert_eq "未通過 prompt Gate 的候選隔日會重試" "1" "$([ -f "$TMP/calls.jsonl" ] && wc -l < "$TMP/calls.jsonl" | tr -d ' ' || printf 0)"

set_scenario "candidate-malformed-alongside-valid"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr" "$TMP/calls.jsonl"
run_wrapper_expect_success
if grep -q 'stealth/new-alpha' "$TMP/report.md" && grep -q 'stealth/malformed-alpha.*candidate metadata is malformed' "$TMP/report.md"; then
  pass "單筆 malformed candidate 不阻斷其他候選"
else
  fail "malformed candidate 中止或吞掉其他候選"
fi
assert_eq "只對 valid candidate 執行兩個 Gate" "2" "$([ -f "$TMP/calls.jsonl" ] && wc -l < "$TMP/calls.jsonl" | tr -d ' ' || printf 0)"

for scenario in candidate-extra-tool candidate-extra-argument; do
  set_scenario "$scenario"
  rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr" "$TMP/calls.jsonl"
  run_wrapper_expect_success
  if grep -q '^# ccp-free 候選檢查' "$TMP/report.md" && grep -q 'forced tool marker mismatch' "$TMP/report.md"; then
    pass "$scenario 無法通過 exact tool Gate"
  else
    fail "$scenario 被 synthetic Gate 誤接受"
  fi
done

set_scenario "candidate-chat-redirect"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr" "$TMP/calls.jsonl"
run_wrapper_expect_success
if grep -q '^# ccp-free 候選檢查' "$TMP/report.md" && ! grep -q '"path": "/redirect-target"' "$TMP/calls.jsonl"; then
  pass "synthetic POST redirect 被拒且不轉送 bearer／prompt"
else
  fail "synthetic POST redirect 被 follow"
fi

set_scenario "candidate-extra-fee"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr" "$TMP/calls.jsonl"
run_wrapper_expect_success
assert_eq "有額外 request 費用的候選被排除" "__SILENT__" "$(cat "$TMP/report.md" 2>/dev/null || true)"
assert_eq "有額外費用的候選不跑 synthetic Gate" "0" "$([ -f "$TMP/calls.jsonl" ] && wc -l < "$TMP/calls.jsonl" | tr -d ' ' || printf 0)"

set_scenario "models-429"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
run_wrapper_expect_success
if grep -q '^# ccp-free watch 監控失敗' "$TMP/report.md" && ! grep -q 'Active route 已退場' "$TMP/report.md"; then
  pass "單次 429 回報監控失敗但不誤判退場"
else
  fail "單次 429 被誤判或靜默"
fi

set_scenario "models-redirect"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr" "$TMP/calls.jsonl"
run_wrapper_expect_success
REDIRECT_CALLS="$([ -f "$TMP/calls.jsonl" ] && wc -l < "$TMP/calls.jsonl" | tr -d ' ' || printf 0)"
if grep -q '^# ccp-free watch 監控失敗' "$TMP/report.md" && [ "$REDIRECT_CALLS" -eq 0 ]; then
  pass "authenticated API redirect 被拒且不轉送 bearer"
else
  fail "authenticated API redirect 洩漏或被誤接受"
fi

for scenario in models-malformed key-malformed active-endpoints-malformed; do
  set_scenario "$scenario"
  rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
  if run_wrapper && grep -q '^# ccp-free watch 監控失敗' "$TMP/report.md" && ! grep -q 'Active route 已退場\|Active route 無法服務' "$TMP/report.md"; then
    pass "$scenario 的 malformed 200 明確回報監控失敗"
  else
    fail "$scenario 的 malformed 200 被誤判或靜默"
  fi
done

set_scenario "candidate-missing-status"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr" "$TMP/calls.jsonl"
run_wrapper_expect_success
assert_eq "缺少 endpoint status 的 candidate 不跑 Gate" "0" "$([ -f "$TMP/calls.jsonl" ] && wc -l < "$TMP/calls.jsonl" | tr -d ' ' || printf 0)"

set_scenario "models-secret-error"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr" "$TMP/watch.log"
if run_wrapper; then
  pass "含 secret 的 HTTP error 仍以 report 成功交付"
else
  fail "含 secret 的 HTTP error 不應破壞 channel"
fi
SECRET_ERROR_HITS=0
for path in "$TMP/report.md" "$TMP/baseline.json" "$TMP/stdout" "$TMP/stderr" "$TMP/watch.log"; do
  if [ -f "$path" ] && grep -Fq 'fake-openrouter-secret' "$path"; then
    SECRET_ERROR_HITS=$((SECRET_ERROR_HITS + 1))
  fi
done
if grep -q '\[REDACTED\]' "$TMP/report.md" && [ "$SECRET_ERROR_HITS" -eq 0 ]; then
  pass "exception 與 error body 先 redaction 再落產物"
else
  fail "exception redaction 未封住 secret"
fi

set_scenario "candidate-secret-id"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr" "$TMP/watch.log" "$TMP/calls.jsonl"
run_wrapper_expect_success
CANDIDATE_SECRET_HITS=0
for path in "$TMP/report.md" "$TMP/baseline.json" "$TMP/stdout" "$TMP/stderr" "$TMP/watch.log"; do
  if [ -f "$path" ] && grep -Fq 'fake-openrouter-secret' "$path"; then
    CANDIDATE_SECRET_HITS=$((CANDIDATE_SECRET_HITS + 1))
  fi
done
assert_eq "含 secret／非法字元的 provider model ID 不落產物" "0" "$CANDIDATE_SECRET_HITS"
assert_eq "不安全 model ID 不送 synthetic Gate" "0" "$([ -f "$TMP/calls.jsonl" ] && wc -l < "$TMP/calls.jsonl" | tr -d ' ' || printf 0)"

set_scenario "healthy"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
run_wrapper_expect_success
set_scenario "terms-change"
rm -f "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
run_wrapper_expect_success
if grep -q '^# ccp-free 需要處理' "$TMP/report.md" && grep -q 'Stealth terms 已變更' "$TMP/report.md"; then
  pass "Stealth terms hash 變更產生 finding"
else
  fail "Stealth terms hash 變更缺少 finding"
fi

set_scenario "healthy"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr" "$TMP/calls.jsonl"
run_wrapper_expect_success
set_scenario "candidate-terms-change"
rm -f "$TMP/report.md" "$TMP/stdout" "$TMP/stderr" "$TMP/calls.jsonl"
run_wrapper_expect_success
if grep -q '已通過 metadata 與 synthetic Gate' "$TMP/report.md" && grep -q 'Stealth terms 已變更' "$TMP/report.md"; then
  pass "同輪 candidate 與 terms transition 都會呈現"
else
  fail "candidate finding 吞掉 terms transition"
fi

set_scenario "healthy"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
if CCP_FREE_WATCH_NOW_OVERRIDE="not-a-timestamp" run_wrapper && grep -q '^# ccp-free watch 監控失敗' "$TMP/report.md"; then
  pass "無效 now 設定仍交付 error report"
else
  fail "無效 now 設定跳過 error report"
fi
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
if CCP_FREE_WATCH_MIN_CONTEXT_OVERRIDE="abc" run_wrapper && grep -q '^# ccp-free watch 監控失敗' "$TMP/report.md"; then
  pass "無效 context 設定仍交付 error report"
else
  fail "無效 context 設定跳過 error report"
fi
set_scenario "expiring-key"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
if CCP_FREE_WATCH_NOW_OVERRIDE="2026-08-24T00:00:00" run_wrapper && grep -q 'API key 將於 1 天內到期' "$TMP/report.md"; then
  pass "naive now 明確按 UTC 處理"
else
  fail "naive now 與 aware expiry 無法比較"
fi

set_scenario "healthy"
printf '%s\n' 'export OPENROUTER_API_KEY=fake-openrouter-secret' > "$TMP/runtime.env"
chmod 600 "$TMP/runtime.env"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
if run_wrapper && [ "$(cat "$TMP/report.md")" = "__SILENT__" ]; then
  pass "dotenv export prefix 可安全解析"
else
  fail "dotenv export prefix 被誤判為缺 key"
fi
printf '%s\n' 'OPENROUTER_API_KEY=fake-openrouter-secret' > "$TMP/runtime.env"
chmod 644 "$TMP/runtime.env"
rm -f "$TMP/baseline.json" "$TMP/report.md" "$TMP/stdout" "$TMP/stderr"
run_wrapper_expect_success
if grep -q '^# ccp-free watch 監控失敗' "$TMP/report.md" && grep -q 'expected 0o600' "$TMP/report.md"; then
  pass "credential 權限錯誤可見且不讀 secret"
else
  fail "credential 權限錯誤缺少 report"
fi
chmod 600 "$TMP/runtime.env"

LEAKS=0
for path in "$TMP/report.md" "$TMP/baseline.json" "$TMP/stdout" "$TMP/stderr" "$TMP/watch.log" "$TMP/calls.jsonl"; do
  if [ -f "$path" ] && grep -Fq 'fake-openrouter-secret' "$path"; then
    LEAKS=$((LEAKS + 1))
  fi
done
assert_eq "provider key 不進任何 channel 產物" "0" "$LEAKS"

printf '%s\n' '----'
printf '%s PASS / %s FAIL\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
