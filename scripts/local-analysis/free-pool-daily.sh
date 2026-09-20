#!/bin/bash
# free-pool-daily.sh — /daily-local workflow 的 free-pool channel（2026-09-21 建）。
#
# kind: 'shell'——零 LLM。偵測 free / free-smart 模型池各來源的三層死法：
#   過程沒開（port / process）／認證・額度死（402、ready 數、餘額）／位址漂移（ngrok 現行 URL 實打）。
# 可用證據語義（使用者拍板的 hello 簡化）：對 relay config 內每條 free/free-smart 腿（跳過
# disabled、按 base-url+model 去重、動態讀檔新腿自動納入）打一發 hello 小請求——200+非空
# content＝「此刻這條腿真的能出活」；認證死／額度死／通道死一發現形，「未驗證」態被主動
# 探測壓縮掉。hello 失敗分級：step- 402 或 content 空＝B（花錢來源）、其他腿死＝C、
# 429（無 deployments／cooldown 字樣）或探測環境壞＝D（判不出來，fail-loud）；
# 429 但回應體含 No deployments／cooldown＝C（上游配額死，litellm 對冷卻耗盡的表達方式）。
# hello 逾時 60 秒——mimo 這類引擎文件記載延遲可達 30 秒，20 秒會假陽。
# 全綠寫 __SILENT__（證據落在 LOG 的 hello 行）；任一來源異常才產出報告，嚴重度排序
# A→B→C→D：全池級（relay / litellm 掛）→ 花錢來源（stepfun 額度）→ 池內其他來源 →
# 探測失敗／config 類。動工依據 gate-authoring；2026-09-21 使用者拍板
# 「獨立 free-pool 軸、不併 codex-cdp」——codex-cdp 管連線地基、本軸管供應鏈三層死法。
#
# Threat model（提示型 detector）：
#   - 不保證即時性：日頻；分鐘級中斷由鏈的 failover 自己撐，本軸管「隔天要知道要修什麼」
#   - 不自動修、不拔腿；處置歸使用者（trial review / 手動）
#   - 探測失敗 ≠ 綠燈：判不出來一律標 ⚠ unknown（fail-loud），不靜默放行
#   - 刻意不做：派工流量壓測、自動修復、通知管道（消費走 daily-local digest）
#
# read-only：唯讀探測；唯二寫入是 OUT 與 LOG。workflow channel 非 CC hook，不進 cc-hooks.json。
# Wrapper 路徑慣例：local-analysis.js 的 W 常數 = ~/code/social-info/scripts/local-analysis。

cd /
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/Users/linhancheng/.local/bin"
export PATH
PY_YAML="${FREE_POOL_PY_YAML:-/opt/homebrew/bin/python3}"
[[ -x "$PY_YAML" ]] || PY_YAML="$(command -v python3 || echo python3)"

SI="/Users/linhancheng/code/social-info"
OUT_DIR="${FREE_POOL_OUT_DIR:-$SI/reports/local-analysis}"
LOG_DIR="${FREE_POOL_LOG_DIR_PATH:-$SI/logs}"
DATE="${LOCAL_ANALYSIS_DATE:-$(date +%F)}"
OUT="$OUT_DIR/$DATE-free-pool.md"
LOG="$LOG_DIR/local-analysis-free-pool-$DATE.log"
mkdir -p "$OUT_DIR" "$LOG_DIR"

RELAY_CONFIG="${FREE_POOL_RELAY_CONFIG:-$HOME/.cli-proxy-api/config.yaml}"
CLINE_ACCOUNTS="${FREE_POOL_CLINE_ACCOUNTS:-$HOME/.cline2api/.cline-accounts.json}"
LITELLM_LOG_DIR="${FREE_POOL_LITELLM_LOG_DIR:-$HOME/.local/state/litellm}"
STEPFUN_PLIST="${FREE_POOL_STEPFUN_PLIST:-$HOME/Library/LaunchAgents/com.gggodlin.litellm-proxy.plist}"
STEPFUN_URL="${FREE_POOL_STEPFUN_URL:-https://api.stepfun.com/v1/accounts}"
STEPFUN_FLOOR="${FREE_POOL_STEPFUN_FLOOR:-2}"
PORT_RELAY="${FREE_POOL_PORT_RELAY:-8317}"
PORT_LITELLM="${FREE_POOL_PORT_LITELLM:-8000}"
PORT_CLINE="${FREE_POOL_PORT_CLINE:-3457}"
PORT_WB="${FREE_POOL_PORT_WB:-3010}"
PORT_AR="${FREE_POOL_PORT_AR:-8002}"
WB_URL="${FREE_POOL_WB_URL:-http://127.0.0.1:$PORT_WB}"
AR_URL="${FREE_POOL_AR_URL:-http://127.0.0.1:$PORT_AR}"
MIMO_HEALTH="${FREE_POOL_MIMO_HEALTH:-http://127.0.0.1:8320/health}"

FINDINGS="$LOG_DIR/.free-pool-findings-$DATE.tmp"
: > "$FINDINGS"

add_finding() { printf '%s\t%s\n' "$1" "$2" >> "$FINDINGS"; }

port_probe() { /usr/bin/nc -z 127.0.0.1 "$1" 2>/dev/null; }

http_code() {
  local c
  c=$(/usr/bin/curl -sS -o /dev/null -m 4 -w '%{http_code}' "$1" 2>/dev/null) || true
  printf '%s' "${c:-000}"
}

{
  echo "free-pool daily probe $DATE $(date '+%H:%M:%S')"
  echo "relay_config=$RELAY_CONFIG"

  if ! port_probe "$PORT_RELAY"; then
    add_finding A "[relay] :${PORT_RELAY} 未在聽——全池死（launchd 應自摔重啟，若持續紅查 ~/Library/LaunchAgents/com.philip.cli-proxy-api）"
  fi
  if ! port_probe "$PORT_LITELLM"; then
    add_finding A "[litellm] :${PORT_LITELLM} 未在聽——經 litellm 的腿（groq/bai/mimo/stepfun）全死"
  fi

  if [[ -r "$STEPFUN_PLIST" ]] && command -v /usr/libexec/PlistBuddy >/dev/null; then
    sf_key=$(/usr/libexec/PlistBuddy -c 'Print :EnvironmentVariables:STEPFUN_KEY' "$STEPFUN_PLIST" 2>/dev/null || true)
    if [[ -z "${sf_key:-}" ]]; then
      add_finding D "[stepfun] plist 讀不到 STEPFUN_KEY——探測不能（fail-loud，非綠燈）"
    else
      sf_json=$(/usr/bin/curl -fsS -m 6 -H "Authorization: Bearer ${sf_key}" "$STEPFUN_URL" 2>/dev/null) || sf_json=""
      if [[ -z "$sf_json" ]]; then
        add_finding D "[stepfun] 帳戶端點探測失敗（網路或 key 失效；不代表額度耗盡）"
      else
        sf_bal=$(printf '%s' "$sf_json" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('balance',0))" 2>/dev/null || echo "?")
        sf_vou=$(printf '%s' "$sf_json" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('total_voucher_balance',0))" 2>/dev/null || echo "?")
        if python3 -c "import sys; b=float('${sf_bal}'); v=float('${sf_vou}'); sys.exit(0 if (b<=0 and v<=0) or b<float('${STEPFUN_FLOOR}') else 1)" 2>/dev/null; then
          add_finding B "[stepfun] 餘額 ¥${sf_bal}（贈金 ¥${sf_vou}）低於地板 ¥${STEPFUN_FLOOR} 或已歸零——拔鏈腿或充值前鏈會自動繞過；trial ccp-stepfun review 處理"
        fi
      fi
      step_log_count=$(
        python3 - "$LITELLM_LOG_DIR" "$DATE" <<'PY'
import json, sys, os, glob, datetime
log_dir, today = sys.argv[1], sys.argv[2]
days = {today}
try:
    days.add((datetime.date.fromisoformat(today) - datetime.timedelta(days=1)).isoformat())
except Exception:
    pass
c402 = c429 = cs = cf = 0
for d in days:
    for path in glob.glob(os.path.join(log_dir, f"calls-{d}.jsonl")):
        for line in open(path, encoding="utf-8", errors="replace"):
            if "step-" not in line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not str(rec.get("model", "")).startswith("step-"):
                continue
            if rec.get("status") == "success":
                cs += 1
            else:
                cf += 1
                blob = line.lower()
                if "402" in blob or "insufficient" in blob:
                    c402 += 1
                elif "429" in blob or "rate" in blob:
                    c429 += 1
print(f"{cs}|{cf}|{c402}|{c429}")
PY
      )
      IFS='|' read -r sf_cs sf_cf sf_402 sf_429 <<< "${step_log_count:-0|0|0|0}"
      if [[ "${sf_402:-0}" -gt 0 ]]; then
        add_finding B "[stepfun] 24h litellm log 有 402/insufficient×${sf_402}——額度耗盡實錘；充值後 kickstart relay 解冷卻"
      fi
      echo "stepfun_log_24h=${sf_cs}勝/${sf_cf}敗 402×${sf_402} 429×${sf_429}"
    fi
  fi

  wb_code=$(http_code "$WB_URL/health")
  if [[ "$wb_code" == "000" ]]; then
    add_finding C "[workbuddy] :${PORT_WB} sidecar 未回應——workbuddy-v41 腿死，鏈會跳過"
  elif [[ "$wb_code" != "200" ]]; then
    add_finding C "[workbuddy] health 回 HTTP ${wb_code}——sidecar 在但狀態異常"
  fi

  ar_code=$(http_code "$AR_URL/health/liveliness")
  if [[ "$ar_code" == "000" ]]; then
    add_finding C "[agentrouter] :${PORT_AR} 未回應——agentrouter-glm 腿死"
  elif [[ "$ar_code" != "200" ]]; then
    add_finding C "[agentrouter] liveliness 回 HTTP ${ar_code}"
  fi

  if port_probe "$PORT_CLINE"; then
    if [[ -r "$CLINE_ACCOUNTS" ]]; then
      cline_stat=$("$PY_YAML" - "$CLINE_ACCOUNTS" "$(date '+%Y-%m-%dT%H:%M:%S')" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("PARSE_FAIL")
    sys.exit(0)
now = sys.argv[2]
accs = d.get("accounts", d if isinstance(d, list) else [])
total = len(accs)
active = sum(1 for a in accs if (a.get("status") or "") == "active")
keys = set()
for a in accs:
    keys.update((a.get("modelCooldowns") or {}).keys())
models = sorted(keys | {"z-ai/glm-5.3-flash", "deepseek/deepseek-v4-flash-0731", "cline-free/deepseek-v4.1-flash"})
parts = []
zeros = []
for m in models:
    n = 0
    for a in accs:
        if (a.get("status") or "") != "active":
            continue
        until = (a.get("modelCooldowns") or {}).get(m) or ""
        if isinstance(until, list):
            until = until[0] if until else ""
        if not until or str(until)[:19] <= now[:19]:
            n += 1
    parts.append(f"{m}={n}")
    if n == 0:
        zeros.append(m)
print(f"{active}/{total}|" + " ".join(parts) + "|" + ",".join(zeros))
PY
      )
      echo "cline_accounts=${cline_stat}"
      if [[ "$cline_stat" == "PARSE_FAIL" || -z "$cline_stat" ]]; then
        add_finding D "[cline] 帳號檔解析失敗：${CLINE_ACCOUNTS}——ready 數無法判定（fail-loud）"
      else
        IFS='|' read -r cline_active cline_avail cline_zeros <<< "$cline_stat"
        if [[ "$cline_active" == "0/"* ]]; then
          add_finding C "[cline] 帳號池 0 active（${cline_active}）——cline 系腿全死；查額度或 namespace 漂移（09-15 V4.1 事件同形）"
        elif [[ -n "$cline_zeros" ]]; then
          add_finding C "[cline] 帳號皆 active（${cline_active}）但模型 ${cline_zeros} 冷卻後 0 個可用帳號——該腿安靜死（cline2api 選號會排除冷卻帳號，pool.go eligible 邏輯）；per-model available：${cline_avail}"
        fi
      fi
    else
      add_finding D "[cline] 帳號檔不可讀：${CLINE_ACCOUNTS}——ready 數無法判定（fail-loud）"
    fi
  else
    add_finding C "[cline2api] :${PORT_CLINE} 未在聽——cline 帳號池腿全死"
  fi

  mimo_code=$(http_code "$MIMO_HEALTH")
  if [[ "$mimo_code" == "000" ]]; then
    if /usr/bin/pgrep -qf "Xiaomi MiMo AI"; then
      add_finding C "[mimo] Desktop 程序在但 adapter :8320 無回應——引擎狀態異常"
    else
      add_finding C "[mimo] Desktop 未啟動（adapter 無回應）——mimo 腿死；開 app 並確認登入即恢復"
    fi
  elif [[ "$mimo_code" != "200" ]]; then
    add_finding C "[mimo] adapter /health 回 HTTP ${mimo_code}（503=Desktop 開著但未登入）"
  fi

  if [[ -r "$RELAY_CONFIG" ]]; then
    ngrok_rc=0
    ngrok_url=$("$PY_YAML" - "$RELAY_CONFIG" <<'PY'
import sys
try:
    import yaml
except ImportError:
    sys.exit(2)
try:
    cfg = yaml.safe_load(open(sys.argv[1]))
except Exception:
    sys.exit(3)
for p in (cfg or {}).get("openai-compatibility", []):
    if p.get("name") == "atkins-devin-swe2":
        if p.get("disabled"):
            print("DISABLED")
        else:
            print(p.get("base-url", ""))
        break
PY
    ) || ngrok_rc=$?
    if [[ "$ngrok_rc" -eq 2 ]]; then
      add_finding D "[devin-swe2] 探測環境缺 yaml 解析器（${PY_YAML} 無法 import yaml）——drift 軸無法判定（fail-loud）"
    elif [[ "$ngrok_rc" -eq 3 ]]; then
      add_finding D "[devin-swe2] relay config 解析失敗：${RELAY_CONFIG}——drift 軸無法判定（fail-loud）"
    elif [[ "${ngrok_url:-}" == "DISABLED" ]]; then
      echo "devin_swe2_base=（provider 已 disabled，照紀錄略過不探測）"
    elif [[ -n "${ngrok_url:-}" ]]; then
      devin_code=$(http_code "${ngrok_url%/}/models")
      echo "devin_swe2_base=${ngrok_url} code=${devin_code}"
      if [[ "$devin_code" == "000" || "$devin_code" == "404" || "$devin_code" == "502" || "$devin_code" == "503" ]]; then
        add_finding C "[devin-swe2] 現行 ngrok URL（${ngrok_url}）探測 HTTP ${devin_code}——網址可能已漂移（免費隧道重開即變），更新 relay config 前該腿死"
      fi
    else
      echo "devin_swe2_base=（config 確認無 atkins-devin-swe2 provider，該軸不適用）"
    fi
  else
    add_finding D "[relay-config] 讀不到 ${RELAY_CONFIG}——鏈腿清單與 devin URL 無法判定（fail-loud）"
  fi

  hello_rc=0
  hello_out=$("$PY_YAML" - "$RELAY_CONFIG" <<'PY'
import json, sys, time, urllib.request, urllib.error
try:
    import yaml
except ImportError:
    print("HELLO_FAIL\tD\t[hello] 探測環境缺 yaml——全池 hello 證據未取得（fail-loud）")
    sys.exit(0)
try:
    cfg = yaml.safe_load(open(sys.argv[1]))
except Exception:
    print("HELLO_FAIL\tD\t[hello] relay config 解析失敗——全池 hello 證據未取得（fail-loud）")
    sys.exit(0)
probes = {}
for p in (cfg or {}).get("openai-compatibility", []):
    if p.get("disabled"):
        continue
    chain_models = [m for m in p.get("models", []) if m.get("alias") in ("free", "free-smart")]
    if not chain_models:
        continue
    base = (p.get("base-url") or "").rstrip("/")
    key_entries = p.get("api-key-entries") or [{}]
    key = key_entries[0].get("api-key", "")
    for m in chain_models:
        probes.setdefault((base, m.get("name")), (p.get("name", "?"), key))
for (base, model), (pname, key) in sorted(probes.items()):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 400,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        base + "/chat/completions", data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
            msg = ((d.get("choices") or [{}])[0].get("message")) or {}
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
            comp_tok = ((d.get("usage") or {}).get("completion_tokens")) or 0
            print(f"hello ✓ {pname} {model}: {r.status} content={content[:24]!r} reasoning={bool(reasoning)} comp_tok={comp_tok}")
            if not content.strip() and not reasoning.strip() and comp_tok <= 0:
                sev = "B" if str(model).startswith("step-") else "C"
                print(f"HELLO_FAIL\t{sev}\t[{pname}] hello 回 200 但零輸出（content/reasoning/completion_tokens 全空）——回應通道死（{base} model={model}）")
    except urllib.error.HTTPError as e:
        code = e.code
        try:
            body_txt = e.read(200).decode("utf-8", "replace").replace("\n", " ")
        except Exception:
            body_txt = ""
        if code == 429 and ("No deployments" in body_txt or "cooldown" in body_txt):
            sev, kind = "C", "上游 deployment 全數冷卻——配額／key 疑耗盡（litellm 回 429 但語義是腿死）"
        elif code == 429:
            sev, kind = "D", "撞限流未能判定（fail-loud）"
        elif code == 402:
            sev = "B" if str(model).startswith("step-") else "C"
            kind = "額度耗盡"
        elif code in (401, 403):
            sev, kind = "C", f"認證／權限失效（HTTP {code}）"
        else:
            sev, kind = "C", f"HTTP {code}"
        print(f"HELLO_FAIL\t{sev}\t[{pname}] hello 失敗：{kind}（{base} model={model}）上游回應：{body_txt[:120]}")
    except Exception as e:
        print(f"HELLO_FAIL\tC\t[{pname}] hello 連線失敗：{type(e).__name__}（{base} model={model}）")
    time.sleep(1)
PY
  ) || hello_rc=$?
  printf '%s\n' "$hello_out"
  if [[ "${hello_rc:-0}" -ne 0 ]]; then
    add_finding D "[hello] 探測執行失敗（exit ${hello_rc}）——全池可用證據未取得（fail-loud）"
  fi
  while IFS=$'\t' read -r _tag _sev _msg; do
    [[ "$_tag" == "HELLO_FAIL" ]] && add_finding "$_sev" "$_msg"
  done <<< "$hello_out"
} > "$LOG" 2>&1 || true

if [[ -s "$FINDINGS" ]]; then
  {
    echo "# free-pool liveness ${DATE}"
    echo
    echo "結論：⚠ $(wc -l < "$FINDINGS" | tr -d ' ') 個發現（嚴重度排序：全池級→花錢來源→池內其他→探測失敗）"
    echo
    sort -t$'\t' -k1,1 "$FINDINGS" | while IFS=$'\t' read -r _pri msg; do
      echo "- ⚠ ${msg}"
    done
  } > "$OUT"
  rm -f "$FINDINGS"
  echo "free-pool: findings written to $OUT"
else
  rm -f "$FINDINGS"
  printf '__SILENT__\n' > "$OUT"
  echo "free-pool: all green"
fi
