#!/bin/bash
set -uo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/linhancheng/.local/bin:/opt/homebrew/bin"
export PATH

SI="/Users/linhancheng/code/social-info"
DATE="${LOCAL_ANALYSIS_DATE:-$(date +%F)}"
export CCP_FREE_WATCH_DATE="$DATE"
export CCP_FREE_WATCH_OUT="${CCP_FREE_WATCH_OUT:-$SI/reports/local-analysis/$DATE-ccp-free-watch.md}"
export CCP_FREE_WATCH_LOG="${CCP_FREE_WATCH_LOG:-$SI/logs/local-analysis-ccp-free-watch-$DATE.log}"
export CCP_FREE_WATCH_BASELINE="${CCP_FREE_WATCH_BASELINE:-$SI/reports/local-analysis/.ccp-free-watch-baseline.json}"
export CCP_FREE_WATCH_METADATA="${CCP_FREE_WATCH_METADATA:-$HOME/.local/share/ccp-free/install-metadata.json}"
export CCP_FREE_WATCH_MODELS_URL="${CCP_FREE_WATCH_MODELS_URL:-https://openrouter.ai/api/v1/models}"
export CCP_FREE_WATCH_KEY_URL="${CCP_FREE_WATCH_KEY_URL:-https://openrouter.ai/api/v1/key}"
export CCP_FREE_WATCH_API_ROOT="${CCP_FREE_WATCH_API_ROOT:-https://openrouter.ai/api/v1}"
export CCP_FREE_WATCH_TERMS_URL="${CCP_FREE_WATCH_TERMS_URL:-https://openrouter.ai/terms/stealth}"
export CCP_FREE_WATCH_CHAT_URL="${CCP_FREE_WATCH_CHAT_URL:-https://openrouter.ai/api/v1/chat/completions}"

mkdir -p "$(dirname "$CCP_FREE_WATCH_OUT")" "$(dirname "$CCP_FREE_WATCH_LOG")" "$(dirname "$CCP_FREE_WATCH_BASELINE")"

python3 - <<'PY' >> "$CCP_FREE_WATCH_LOG" 2>&1
import datetime
import decimal
import hashlib
import html
import json
import math
import os
import re
import shlex
import stat
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

out_path = Path(os.environ["CCP_FREE_WATCH_OUT"])
baseline_path = Path(os.environ["CCP_FREE_WATCH_BASELINE"])
metadata_path = Path(os.environ["CCP_FREE_WATCH_METADATA"])
models_url = os.environ["CCP_FREE_WATCH_MODELS_URL"]
key_url = os.environ["CCP_FREE_WATCH_KEY_URL"]
api_root = os.environ["CCP_FREE_WATCH_API_ROOT"].rstrip("/")
terms_url = os.environ["CCP_FREE_WATCH_TERMS_URL"]
chat_url = os.environ["CCP_FREE_WATCH_CHAT_URL"]
date = os.environ["CCP_FREE_WATCH_DATE"]
now = None
min_context = 0
secrets = []


def safe_output(value):
  text = str(value)
  for secret in secrets:
    if secret:
      text = text.replace(secret, "[REDACTED]")
  return "".join(character if character >= " " else " " for character in text)


def safe_model_id(value):
  text = str(value or "")
  if not re.fullmatch(r"[A-Za-z0-9._:-]+/[A-Za-z0-9._:-]+", text):
    return None
  if any(secret and secret in text for secret in secrets):
    return None
  return text


def emit(text):
  out_path.write_text(text, encoding="utf-8")
  print(text)


def parse_env(path):
  values = {}
  for line in path.read_text(encoding="utf-8").splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
      continue
    if stripped.startswith("export "):
      stripped = stripped[7:].lstrip()
    if "=" not in stripped:
      continue
    key, raw = stripped.split("=", 1)
    parsed = shlex.split(raw.strip())
    values[key.strip()] = parsed[0] if parsed else ""
  return values


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
  def redirect_request(self, _request, _file_pointer, _code, _message, _headers, _new_url):
    return None


opener = urllib.request.build_opener(NoRedirectHandler())


def request(url, token=None):
  headers = {"Accept": "application/json", "User-Agent": "ccp-free-watch/1"}
  if token:
    headers["Authorization"] = f"Bearer {token}"
  with opener.open(urllib.request.Request(url, headers=headers), timeout=20) as response:
    return response.read()


def normalize_terms(raw):
  text = raw.decode("utf-8", "replace")
  text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
  text = re.sub(r"<[^>]+>", " ", text)
  text = html.unescape(text)
  return re.sub(r"\s+", " ", text).strip().encode("utf-8")


def request_json(url, token=None):
  return json.loads(request(url, token).decode("utf-8"))


def post_json(url, token, payload):
  body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
  headers = {
    "Accept": "application/json",
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "User-Agent": "ccp-free-watch/1",
  }
  request_object = urllib.request.Request(url, data=body, headers=headers, method="POST")
  with opener.open(request_object, timeout=30) as response:
    return json.loads(response.read().decode("utf-8"))


def load_json(path):
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except (FileNotFoundError, json.JSONDecodeError):
    return {}


def require_data(payload, expected_type, label):
  if not isinstance(payload, dict) or payload.get("error") is not None or "data" not in payload:
    raise RuntimeError(f"{label} response schema is invalid")
  data = payload["data"]
  if not isinstance(data, expected_type):
    raise RuntimeError(f"{label} response data has the wrong type")
  return data


def require_endpoints(payload, label):
  data = require_data(payload, dict, label)
  endpoints = data.get("endpoints")
  if not isinstance(endpoints, list):
    raise RuntimeError(f"{label} response endpoints are invalid")
  return endpoints


def endpoint_is_eligible(endpoint):
  if not isinstance(endpoint, dict) or "status" not in endpoint:
    return False
  try:
    status = int(endpoint["status"])
  except (TypeError, ValueError):
    return False
  return status >= 0 and has_only_zero_prices(endpoint.get("pricing") or {})


def parse_time(value):
  parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
  return parsed.replace(tzinfo=datetime.timezone.utc) if parsed.tzinfo is None else parsed


def is_zero_price(value):
  try:
    return decimal.Decimal(str(value)) == 0
  except decimal.InvalidOperation:
    return False


def canonical_price(value):
  try:
    number = decimal.Decimal(str(value))
  except decimal.InvalidOperation:
    return str(value)
  if number == 0:
    return "0"
  return format(number.normalize(), "f")


def has_only_zero_prices(pricing):
  if not is_zero_price(pricing.get("prompt")) or not is_zero_price(pricing.get("completion")):
    return False
  for value in pricing.values():
    if value is None:
      continue
    try:
      if decimal.Decimal(str(value)) != 0:
        return False
    except decimal.InvalidOperation:
      return False
  return True


def atomic_json(path, value):
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
      json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
      handle.write("\n")
    os.replace(temporary, path)
  finally:
    if os.path.exists(temporary):
      os.unlink(temporary)


def record_transition(previous, current, report):
  merged = {**previous, **current}
  comparable = {key: value for key, value in merged.items() if key != "checked_on"}
  prior = {key: previous.get(key) for key in comparable}
  emit(report if prior != comparable else "__SILENT__")
  atomic_json(baseline_path, merged)
  raise SystemExit(0)


def candidate_expiration_is_safe(value):
  if not value:
    return True
  return (parse_time(value) - now).total_seconds() > 7 * 86400


def synthetic_gate(model_id, token):
  provider_policy = {
    "allow_fallbacks": False,
    "require_parameters": True,
    "max_price": {"prompt": "0", "completion": "0"},
  }
  prompt_payload = {
    "model": model_id,
    "provider": provider_policy,
    "messages": [{"role": "user", "content": "Reply with exactly CCP_FREE_WATCH_PROMPT_OK"}],
    "max_tokens": 1024,
  }
  prompt_response = post_json(chat_url, token, prompt_payload)
  prompt_message = (((prompt_response.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
  if prompt_message != "CCP_FREE_WATCH_PROMPT_OK":
    return False, "fixed prompt marker mismatch"
  tool_payload = {
    "model": model_id,
    "provider": provider_policy,
    "messages": [{"role": "user", "content": "Call the required marker tool once."}],
    "max_tokens": 1024,
    "tools": [{
      "type": "function",
      "function": {
        "name": "ccp_free_watch_marker",
        "description": "Return the fixed health marker",
        "parameters": {
          "type": "object",
          "properties": {"marker": {"type": "string"}},
          "required": ["marker"],
          "additionalProperties": False,
        },
      },
    }],
    "tool_choice": {"type": "function", "function": {"name": "ccp_free_watch_marker"}},
  }
  tool_response = post_json(chat_url, token, tool_payload)
  message = ((tool_response.get("choices") or [{}])[0].get("message") or {})
  calls = message.get("tool_calls") or []
  if len(calls) != 1:
    return False, "forced tool marker mismatch"
  function = (calls[0] or {}).get("function") or {}
  if function.get("name") != "ccp_free_watch_marker":
    return False, "forced tool marker mismatch"
  try:
    arguments = json.loads(function.get("arguments") or "{}")
  except json.JSONDecodeError:
    return False, "forced tool marker mismatch"
  if arguments != {"marker": "CCP_FREE_WATCH_TOOL_OK"}:
    return False, "forced tool marker mismatch"
  return True, "prompt and forced tool markers matched"


try:
  now_text = os.environ.get("CCP_FREE_WATCH_NOW")
  now = parse_time(now_text) if now_text else datetime.datetime.now(datetime.timezone.utc)
  min_context = int(os.environ.get("CCP_FREE_WATCH_MIN_CONTEXT", "200000"))
  if min_context <= 0:
    raise ValueError("CCP_FREE_WATCH_MIN_CONTEXT must be positive")
  previous = load_json(baseline_path)
  metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
  secret_env = Path(metadata["secret_env"]).expanduser()
  mode = stat.S_IMODE(secret_env.stat().st_mode)
  if mode != 0o600:
    raise RuntimeError(f"runtime credential mode is {oct(mode)}, expected 0o600")
  token = parse_env(secret_env).get("OPENROUTER_API_KEY", "").strip()
  if not token:
    raise RuntimeError("OPENROUTER_API_KEY is missing")
  secrets.append(token)
  model_ref = str(metadata["model_ref"])
  active_id = safe_model_id(model_ref.removeprefix("open_router/"))
  if active_id is None:
    raise RuntimeError("active model ID is invalid or contains credential material")
  models_payload = request_json(models_url, token)
  models = require_data(models_payload, list, "models API")
  active = next((model for model in models if model.get("id") == active_id), None)
  if active is None:
    current = {
      "active_model": active_id,
      "active_exists": False,
      "issue": "active-model-missing",
      "checked_on": date,
    }
    report = "\n".join([
      f"# ccp-free 需要處理 — {date}",
      "",
      "## 🚨 Active route 已退場",
      "",
      f"- 現象：active model 已從 OpenRouter catalog 消失：`{active_id}`",
      "- 影響：ccp-free 沒有可驗證的 exact model；不得自動 fallback。",
      "- 建議：保持 unavailable，直到選定並驗證 stable 候補。",
      "- 拍板：處理／忽略／延後",
    ])
    record_transition(previous, current, report)
  active_pricing = active.get("pricing") or {}
  prompt_price = active_pricing.get("prompt")
  completion_price = active_pricing.get("completion")
  if not has_only_zero_prices(active_pricing):
    current = {
      "active_model": active_id,
      "active_exists": True,
      "active_prompt_price": canonical_price(prompt_price),
      "active_completion_price": canonical_price(completion_price),
      "issue": "active-model-paid",
      "checked_on": date,
    }
    report = "\n".join([
      f"# ccp-free 需要處理 — {date}",
      "",
      "## 🚨 Active route 不再免費",
      "",
      f"- 現象：`{active_id}` 價格不再是 0/0；prompt={prompt_price}、completion={completion_price}。",
      "- 影響：繼續使用可能產生付費請求。",
      "- 建議：保持 unavailable，直到切換到已驗證的零價 exact model。",
      "- 拍板：處理／忽略／延後",
    ])
    record_transition(previous, current, report)
  active_metadata_issues = []
  active_parameters = set(active.get("supported_parameters") or [])
  if not {"tools", "tool_choice"}.issubset(active_parameters):
    active_metadata_issues.append("缺少 tools／tool_choice")
  try:
    active_context = int(active.get("context_length"))
  except (TypeError, ValueError):
    active_context = 0
  if active_context < min_context:
    active_metadata_issues.append(f"context_length {active_context} 低於 {min_context}")
  try:
    active_expiration_safe = candidate_expiration_is_safe(active.get("expiration_date"))
  except (TypeError, ValueError):
    active_expiration_safe = False
  if not active_expiration_safe:
    active_metadata_issues.append("expiration_date 已到期或在 7 天內")
  if active_metadata_issues:
    current = {
      "active_model": active_id,
      "active_exists": True,
      "active_prompt_price": canonical_price(prompt_price),
      "active_completion_price": canonical_price(completion_price),
      "active_metadata_issues": active_metadata_issues,
      "issue": "active-metadata-invalid",
      "checked_on": date,
    }
    report = "\n".join([
      f"# ccp-free 需要處理 — {date}",
      "",
      "## 🚨 Active model metadata Gate 失敗",
      "",
      *[f"- {issue}" for issue in active_metadata_issues],
      "- 影響：目前 route 不再符合 ccp-free 的 Claude Code 基本契約。",
      "- 建議：保持 unavailable，直到 metadata 恢復或切換已驗證候補。",
      "- 拍板：處理／忽略／延後",
    ])
    record_transition(previous, current, report)
  encoded_model = urllib.parse.quote(active_id, safe="/")
  endpoint_payload = request_json(f"{api_root}/models/{encoded_model}/endpoints", token)
  endpoints = require_endpoints(endpoint_payload, "active endpoint API")
  if not endpoints:
    current = {
      "active_model": active_id,
      "active_exists": True,
      "active_prompt_price": canonical_price(prompt_price),
      "active_completion_price": canonical_price(completion_price),
      "active_endpoint_count": 0,
      "issue": "active-endpoint-empty",
      "checked_on": date,
    }
    report = "\n".join([
      f"# ccp-free 需要處理 — {date}",
      "",
      "## 🚨 Active route 無法服務",
      "",
      f"- 現象：`{active_id}` 沒有可用 endpoint。",
      "- 影響：ccp-free 無法把請求送到 exact model。",
      "- 建議：保持 unavailable，直到 endpoint 恢復或切換到已驗證候補。",
      "- 拍板：處理／忽略／延後",
    ])
    record_transition(previous, current, report)
  active_endpoints = [endpoint for endpoint in endpoints if endpoint_is_eligible(endpoint)]
  if not active_endpoints:
    current = {
      "active_model": active_id,
      "active_exists": True,
      "active_prompt_price": canonical_price(prompt_price),
      "active_completion_price": canonical_price(completion_price),
      "active_endpoint_count": len(endpoints),
      "active_eligible_endpoint_count": 0,
      "issue": "active-endpoint-ineligible",
      "checked_on": date,
    }
    report = "\n".join([
      f"# ccp-free 需要處理 — {date}",
      "",
      "## 🚨 Active route endpoint Gate 失敗",
      "",
      f"- 現象：`{active_id}` 沒有健康且零價 endpoint。",
      "- 影響：目前 endpoint 可能 unavailable 或帶有額外費用。",
      "- 建議：保持 unavailable，直到 endpoint 恢復或切換已驗證候補。",
      "- 拍板：處理／忽略／延後",
    ])
    record_transition(previous, current, report)
  key_payload = request_json(key_url, token)
  key_data = require_data(key_payload, dict, "key API")
  required_key_fields = {"expires_at", "limit", "limit_remaining", "is_free_tier"}
  if not required_key_fields.issubset(key_data):
    raise RuntimeError("key API response fields are incomplete")
  expires_at = key_data.get("expires_at")
  credential_issue = None
  credential_lines = []
  if expires_at:
    expiry = parse_time(expires_at)
    days_left = math.ceil((expiry - now).total_seconds() / 86400)
    if days_left <= 7:
      credential_issue = "credential-expiring"
      timing = "已到期" if days_left <= 0 else f"將於 {days_left} 天內到期"
      credential_lines = [
        "## 🚨 OpenRouter credential 需要更新",
        "",
        f"- 現象：API key {timing}（`{expires_at}`）。",
        "- 影響：到期後 ccp-free 會收到 authentication error。",
        "- 建議：建立新的限額 key並重新驗證 route。",
        "- 拍板：處理／忽略／延後",
      ]
  terms_hash = hashlib.sha256(normalize_terms(request(terms_url))).hexdigest()
  qualified = []
  candidate_diagnostics = []
  for model in models:
    model_id = safe_model_id(model.get("id"))
    if model_id is None or model_id == active_id:
      continue
    searchable = " ".join([
      model_id,
      str(model.get("name") or ""),
      str(model.get("description") or ""),
    ]).lower()
    if not any(marker in searchable for marker in ("stealth", "alpha", "beta", "preview", "experimental")):
      continue
    if not any(marker in searchable for marker in ("coding", "software engineering", "agentic", "programming")):
      continue
    if not has_only_zero_prices(model.get("pricing") or {}):
      continue
    parameters = set(model.get("supported_parameters") or [])
    if not {"tools", "tool_choice"}.issubset(parameters):
      continue
    try:
      candidate_context = int(model.get("context_length") or 0)
      candidate_expiration_safe = candidate_expiration_is_safe(model.get("expiration_date"))
    except (TypeError, ValueError):
      candidate_diagnostics.append((model_id, "candidate metadata is malformed"))
      continue
    if candidate_context < min_context or not candidate_expiration_safe:
      continue
    candidate_id = urllib.parse.quote(model_id, safe="/")
    try:
      candidate_payload = request_json(f"{api_root}/models/{candidate_id}/endpoints", token)
      candidate_endpoints = require_endpoints(candidate_payload, "candidate endpoint API")
    except Exception as error:
      candidate_diagnostics.append((model_id, f"endpoint probe failed: {type(error).__name__}"))
      continue
    eligible_endpoints = [endpoint for endpoint in candidate_endpoints if endpoint_is_eligible(endpoint)]
    if not eligible_endpoints:
      continue
    qualified.append(model_id)

  previous_candidates = set(previous.get("candidate_ids") or [])
  new_candidates = [model_id for model_id in qualified if model_id not in previous_candidates]
  passed_candidates = []
  for model_id in new_candidates:
    try:
      passed, diagnostic = synthetic_gate(model_id, token)
    except Exception as error:
      passed, diagnostic = False, f"synthetic Gate failed: {type(error).__name__}"
    if passed:
      passed_candidates.append(model_id)
    else:
      candidate_diagnostics.append((model_id, diagnostic))

  state = {
    "active_model": active_id,
    "active_exists": True,
    "active_prompt_price": canonical_price(prompt_price),
    "active_completion_price": canonical_price(completion_price),
    "active_endpoint_count": len(endpoints),
    "key_expires_at": expires_at,
    "credential_issue": credential_issue,
    "key_limit": key_data.get("limit"),
    "key_limit_remaining": key_data.get("limit_remaining"),
    "is_free_tier": key_data.get("is_free_tier"),
    "terms_sha256": terms_hash,
    "candidate_ids": sorted((previous_candidates & set(qualified)) | set(passed_candidates)),
    "checked_on": date,
  }
  terms_changed = bool(previous.get("terms_sha256") and previous.get("terms_sha256") != terms_hash)
  credential_changed = bool(credential_issue and (
    previous.get("credential_issue") != credential_issue or previous.get("key_expires_at") != expires_at
  ))
  lines = []
  if passed_candidates or candidate_diagnostics or terms_changed or credential_changed:
    if passed_candidates and not candidate_diagnostics and not terms_changed and not credential_changed:
      lines.extend([f"# ccp-free 新候選 — {date}", ""])
    elif candidate_diagnostics and not passed_candidates and not terms_changed and not credential_changed:
      lines.extend([f"# ccp-free 候選檢查 — {date}", ""])
    else:
      lines.extend([f"# ccp-free 需要處理 — {date}", ""])
  if passed_candidates:
    lines.extend(["## 🟡 已通過 metadata 與 synthetic Gate", ""])
    for model_id in passed_candidates:
      lines.append(f"- `{model_id}`：零 token 價格、tools／tool_choice、context、endpoint、固定 prompt 與 forced tool call 均通過。")
    lines.extend([
      "",
      "- 尚未驗證：Harbor 真實 task 表現與長期 availability。",
      "- 建議：決定是否開 Harbor trial；本 channel 不會自動切換。",
      "- 拍板：處理／忽略／延後",
      "",
    ])
  if candidate_diagnostics:
    lines.extend(["## ⚠️ 候選未通過 Gate", ""])
    lines.extend(f"- `{model_id}`：{diagnostic}" for model_id, diagnostic in candidate_diagnostics)
    lines.extend(["", "未產生上述候選的切換建議；route 未修改。", ""])
  if credential_changed:
    lines.extend(credential_lines + [""])
  if terms_changed:
    lines.extend([
      "## ⚠️ Stealth terms 已變更",
      "",
      "- 現象：OpenRouter Stealth Terms 內容 hash 與前次 baseline 不同。",
      "- 根因：未查",
      "- 建議：重新閱讀條款後決定是否維持 active preview。",
      "- 拍板：處理／忽略／延後",
    ])
  emit("\n".join(lines).rstrip() if lines else "__SILENT__")
  atomic_json(baseline_path, state)
except Exception as error:
  emit("\n".join([
    f"# ccp-free watch 監控失敗 — {date}",
    "",
    "## 🚨 無法判定 ccp-free route 狀態",
    "",
    f"- 現象：{safe_output(type(error).__name__ + ': ' + str(error))}",
    "- 根因：未查",
    "- 處置：處理／忽略／延後",
  ]))
PY
