#!/bin/bash
# free-pool-daily.sh — /daily-local workflow 的 free-pool channel（2026-09-21 建）。
#
# kind: 'shell'——零 LLM。偵測 free / free-smart 模型池各腿的三層死法：
#   過程沒開（gateway / port）／認證・額度死（402、401/403、429）／腿在 gateway 上解析不到。
# 拓樸（2026-10-15 改）：Magpie 當天正式接管號池（證據 ~/.magpie-machine/evidence/
#   formal-pool-cutover-20261015.json、formal-retired-services-20261015.json、
#   formal-cpa-retirement-20261015.json），litellm-proxy(:8000)、cline2api(:3457)、CPA、
#   Agent2API trial 同日退役，原本對它們的 port／帳號池／ngrok 探測因此全拿掉；
#   腿清單改讀 Magpie providers.json 內 free／free-smart 兩個 group 的 members 聯集
#   （動態讀檔，新腿自動納入、provider 標 off 的跳過），經 Magpie gateway 各打一發 hello。
#   - agentrouter :8002 仍探：4 個 legacy provider（agentrouter-glm／astra 與 -smart 版）還以它為後端，
#     cutover 證據對這塊寫 HOLD（Prepared not applied）。gateway 看不到 :8002 本身死活、只看到 hello 逾時，
#     分開探才分得出「gateway 通、後端死」。
#   - :8317 現由 launchd com.philip.relay-backend-switch 提供，仍探 port，掛了要知道。
#   - cline 帳號池健康（active 數、模型冷卻）現在在 Magpie 的 logins.json；本輪不覆蓋，
#     cline 腿只靠 hello 看得到「整條死」，看不到「帳號剩幾個」。
#   - legacy member 是 unlisted、不出現在 /v1/models，但 gateway 仍會路由，所以照 member 字串直接當 model 打；
#     打回 404 且不在 /v1/models 才是 providers.json 與 gateway 對不上，產 D 級 fail-loud，不靜默跳過。
# 可用證據語義（使用者拍板的 hello 簡化）：每條腿打一發 hello 小請求——200+非空
# content＝「小請求存活」：只證明此刻一發小請求能過，不代表正常大小的請求或白天沒出事
# （那段交給下方「過去 24 小時」）；認證死／額度死／通道死一發現形。hello 失敗分級：step- 402 或 content 空＝B（花錢來源）、其他腿死＝C、
# 429（無 deployments／cooldown 字樣）或探測環境壞＝D（判不出來，fail-loud）；
# 429 但回應體含 No deployments／cooldown＝C（上游配額死，上游對冷卻耗盡的表達方式）。
# hello 逾時 60 秒——mimo 這類引擎文件記載延遲可達 30 秒，20 秒會假陽。
# 全綠寫 __SILENT__（證據落在 LOG 的 hello 行）；任一來源異常才產出報告，嚴重度排序
# A→B→C→D：全池級（gateway / relay 掛）→ 花錢來源（2026-09-24 stepfun 拔除後暫無）→ 池內其他來源 →
# 探測失敗／config 類。動工依據 gate-authoring；2026-09-21 使用者拍板
# 「獨立 free-pool 軸、不併 codex-cdp」——codex-cdp 管連線地基、本軸管供應鏈三層死法。
#
# 過去 24 小時段（2026-09-28 relay-watch 06 號票）：呼叫 cliproxyapi-setup 的
# `relay-watch report`，把整個 relay 池過去 24 小時的門檻 alias、低樣本故障、額度拒絕、
# 覆蓋缺口與自檢接在 hello 段後面；門檻定義只在 relay_watch.py，和 30 分鐘排程共用。
# hello 有發現或 24 小時段有異常就出報告；兩者都沒事才 __SILENT__。report 失敗（非零、
# 找不到檔、空輸出）時本腳本非零退出，run-shell-channel 回報失敗、digest 顯示「偵測
# channel 失敗」，不退回只看 hello 的全綠。
# 資料來源與覆蓋限制由 relay-watch 報告，不在 wrapper 覆寫來源判斷。
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
PY="/opt/homebrew/bin/python3"
[[ -x "$PY" ]] || PY="$(command -v python3 || echo python3)"

SI="/Users/linhancheng/code/social-info"
OUT_DIR="${FREE_POOL_OUT_DIR:-$SI/reports/local-analysis}"
LOG_DIR="${FREE_POOL_LOG_DIR_PATH:-$SI/logs}"
DATE="${LOCAL_ANALYSIS_DATE:-$(date +%F)}"
OUT="$OUT_DIR/$DATE-free-pool.md"
LOG="$LOG_DIR/local-analysis-free-pool-$DATE.log"
mkdir -p "$OUT_DIR" "$LOG_DIR"

MAGPIE_PROVIDERS="${FREE_POOL_MAGPIE_PROVIDERS:-$HOME/.magpie-machine/home/.config/magpie/providers.json}"
MAGPIE_URL="${FREE_POOL_MAGPIE_URL:-http://127.0.0.1:18417/v1}"
PORT_RELAY="${FREE_POOL_PORT_RELAY:-8317}"
PORT_AR="${FREE_POOL_PORT_AR:-8002}"
AR_URL="${FREE_POOL_AR_URL:-http://127.0.0.1:$PORT_AR}"
RELAY_WATCH="${FREE_POOL_RELAY_WATCH:-$HOME/Desktop/projects/cliproxyapi-setup/tools/relay-watch/relay_watch.py}"

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
  echo "magpie_url=$MAGPIE_URL"
  echo "magpie_providers=$MAGPIE_PROVIDERS"

  gateway_code=$(http_code "${MAGPIE_URL%/}/models")
  echo "magpie_models_code=${gateway_code}"
  if [[ "$gateway_code" != "200" ]]; then
    add_finding A "[magpie] gateway 未回應——全池死（launchd com.philip.magpie-machine）"
  fi
  if ! port_probe "$PORT_RELAY"; then
    add_finding A "[relay] :${PORT_RELAY} 未在聽（launchd 應自摔重啟，若持續紅查 ~/Library/LaunchAgents/com.philip.relay-backend-switch）"
  fi

  ar_code=$(http_code "$AR_URL/health/liveliness")
  if [[ "$ar_code" == "000" ]]; then
    add_finding C "[agentrouter] :${PORT_AR} 未回應——agentrouter 系 legacy 腿死"
  elif [[ "$ar_code" != "200" ]]; then
    add_finding C "[agentrouter] liveliness 回 HTTP ${ar_code}"
  fi

  # gateway 不通時逐腿 hello 只會產出一排連線失敗，掩蓋 A 級那一條；全池死已經夠響。
  hello_out=""
  if [[ "$gateway_code" == "200" ]]; then
    hello_rc=0
    hello_out=$("$PY" - "$MAGPIE_PROVIDERS" "$MAGPIE_URL" <<'PY'
import json, os, sys, time, urllib.request, urllib.error
providers_path, base = sys.argv[1], sys.argv[2].rstrip("/")
key = os.environ.get("FREE_POOL_MAGPIE_KEY") or ""
try:
    with open(providers_path) as f:
        cfg = json.load(f)
    providers = {p["id"]: p for p in cfg.get("providers", []) if p.get("id")}
    groups = {g.get("id"): g.get("members") or [] for g in cfg.get("groups", [])}
except Exception:
    print("HELLO_FAIL\tD\t[hello] Magpie providers.json 讀取或解析失敗——腿清單無法判定，全池 hello 證據未取得（fail-loud）")
    sys.exit(0)
members = []
for gid in ("free", "free-smart"):
    for m in groups.get(gid, []):
        if m not in members:
            members.append(m)
if not members:
    print("HELLO_FAIL\tD\t[hello] providers.json 內 free／free-smart group 沒有任何 member——腿清單為空，全池 hello 證據未取得（fail-loud）")
    sys.exit(0)
try:
    with urllib.request.urlopen(base + "/models", timeout=10) as r:
        listed = {m.get("id") for m in json.loads(r.read()).get("data", [])}
except Exception:
    listed = set()
for member in members:
    pid = member.split("/", 1)[0]
    prov = providers.get(pid) or {}
    pname = prov.get("name") or pid
    if prov.get("off") is True:
        print(f"hello - {pname} {member}: provider off，略過")
        continue
    body = json.dumps({
        "model": member,
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 400,
        "temperature": 0,
        "metadata": {"budget_mode": "diagnostic"},
    }).encode()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(base + "/chat/completions", data=body, method="POST", headers=headers)
    basename = member.rsplit("/", 1)[-1]
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
            msg = ((d.get("choices") or [{}])[0].get("message")) or {}
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
            comp_tok = ((d.get("usage") or {}).get("completion_tokens")) or 0
            print(f"hello ✓ {pname} {member}: {r.status} content={content[:24]!r} reasoning={bool(reasoning)} comp_tok={comp_tok}")
            if not content.strip() and not reasoning.strip() and comp_tok <= 0:
                sev = "B" if basename.startswith("step-") else "C"
                print(f"HELLO_FAIL\t{sev}\t[{pname}] hello 回 200 但零輸出（content/reasoning/completion_tokens 全空）——回應通道死（{base} model={member}）")
    except urllib.error.HTTPError as e:
        code = e.code
        try:
            body_txt = e.read(200).decode("utf-8", "replace").replace("\n", " ")
        except Exception:
            body_txt = ""
        if code == 404 and member not in listed:
            sev, kind = "D", "member 無法在 gateway 解析（HTTP 404 且不在 /v1/models）——providers.json 與 gateway 對不上，該腿無法判定"
        elif code == 429 and ("No deployments" in body_txt or "cooldown" in body_txt):
            sev, kind = "C", "上游 deployment 全數冷卻——配額／key 疑耗盡（429 但語義是腿死）"
        elif code == 429:
            sev, kind = "D", "撞限流未能判定（fail-loud）"
        elif code == 402:
            sev = "B" if basename.startswith("step-") else "C"
            kind = "額度耗盡"
        elif code in (401, 403):
            sev, kind = "C", f"認證／權限失效（HTTP {code}）"
        else:
            sev, kind = "C", f"HTTP {code}"
        print(f"HELLO_FAIL\t{sev}\t[{pname}] hello 失敗：{kind}（{base} model={member}）上游回應：{body_txt[:120]}")
    except Exception as e:
        print(f"HELLO_FAIL\tC\t[{pname}] hello 連線失敗：{type(e).__name__}（{base} model={member}）")
    time.sleep(1)
PY
    ) || hello_rc=$?
    if [[ "${hello_rc:-0}" -ne 0 ]]; then
      add_finding D "[hello] 探測執行失敗（exit ${hello_rc}）——全池可用證據未取得（fail-loud）"
    fi
  else
    echo "hello=略過（gateway /models HTTP ${gateway_code}）"
  fi
  printf '%s\n' "$hello_out"
  while IFS=$'\t' read -r _tag _sev _msg; do
    [[ "$_tag" == "HELLO_FAIL" ]] && add_finding "$_sev" "$_msg"
  done <<< "$hello_out"
} > "$LOG" 2>&1 || true

day_rc=0
day_raw=$("$PY" "$RELAY_WATCH" report 2>>"$LOG") || day_rc=$?
if [[ "$day_rc" -ne 0 || -z "$day_raw" ]]; then
  echo "relay-watch report 失敗（exit ${day_rc}）：${RELAY_WATCH}" >> "$LOG"
  rm -f "$FINDINGS"
  echo "free-pool: relay-watch report failed (exit ${day_rc}), see $LOG" >&2
  exit 1
fi
day_section="$day_raw"
printf '%s\n' "$day_section" >> "$LOG"
day_clean=false
grep -qx '過去 24 小時無異常' <<< "$day_section" && day_clean=true

if [[ -s "$FINDINGS" ]]; then
  {
    echo "# free-pool liveness ${DATE}"
    echo
    echo "結論：⚠ $(wc -l < "$FINDINGS" | tr -d ' ') 個發現（嚴重度排序：全池級→花錢來源→池內其他→探測失敗）"
    echo
    sort -t$'\t' -k1,1 "$FINDINGS" | while IFS=$'\t' read -r _pri msg; do
      echo "- ⚠ ${msg}"
    done
    echo
    printf '%s\n' "$day_section"
  } > "$OUT"
  rm -f "$FINDINGS"
  echo "free-pool: findings written to $OUT"
elif [[ "$day_clean" == false ]]; then
  {
    echo "# free-pool liveness ${DATE}"
    echo
    echo "結論：小請求存活（free／free-smart 各腿 hello 通過，未發現程序、額度或位址異常）"
    echo
    printf '%s\n' "$day_section"
  } > "$OUT"
  rm -f "$FINDINGS"
  echo "free-pool: 24h findings written to $OUT"
else
  rm -f "$FINDINGS"
  printf '__SILENT__\n' > "$OUT"
  echo "free-pool: all green"
fi
