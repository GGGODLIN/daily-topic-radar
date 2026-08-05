#!/bin/bash
# vpn-precheck.sh 回歸測試
#
# 本檔在契約測試底下：改 scripts/vpn-precheck.sh 或 run-daily.sh 的 pre-check 段後必跑
#   bash scripts/vpn-precheck.test.sh
#
# 覆蓋 gate-authoring 三件 fixture：known-good（該放行）/ known-bad（該攔）/ 邊界（鎖住線的位置）。
# 特別涵蓋「陰性路徑測過 ≠ 測過」——exit 10 那條會觸發的分支用假 org 強制走完整條，
# 且驗證 run-daily.sh 在 set -euo pipefail 下不會被 exit 10 弄死。

set -uo pipefail
cd "$(dirname "$0")/.."
SCRIPT=scripts/vpn-precheck.sh
PASS=0; FAIL=0

t() {
  local name="$1" want="$2" got="$3"
  if [ "$want" = "$got" ]; then PASS=$((PASS+1)); printf "  ✅ %-52s exit=%s\n" "$name" "$got"
  else FAIL=$((FAIL+1)); printf "  ❌ %-52s want=%s got=%s\n" "$name" "$want" "$got"; fi
}

# stub curl：讓測試能模擬 ipinfo 的各種回應而不打真實網路
STUB=$(mktemp -d)
trap 'rm -rf "$STUB"' EXIT
mk_curl() {
  printf '#!/bin/bash\nprintf %%s %s\n' "$(printf '%q' "$1")" > "$STUB/curl"
  chmod +x "$STUB/curl"
}

echo "── known-good：該放行（非雲端出口）──"
for org in \
  "AS3462 Data Communication Business Group" \
  "AS4713 NTT Communications Corporation" \
  "AS3462 HiNet" ; do
  VPN_PRECHECK_FAKE_ORG="$org" bash "$SCRIPT" >/dev/null 2>&1
  t "住宅/電信 ASN: ${org:0:34}" 0 $?
done

echo "── known-bad：該攔（雲端出口）──"
for org in \
  "AS16509 Amazon.com, Inc." \
  "AS15169 Google LLC" \
  "AS8075 Microsoft Corporation" \
  "AS14061 DigitalOcean, LLC" \
  "AS24940 Hetzner Online GmbH" \
  "AS20473 The Constant Company, LLC" ; do
  VPN_PRECHECK_FAKE_ORG="$org" bash "$SCRIPT" >/dev/null 2>&1
  t "雲端 ASN: ${org:0:34}" 10 $?
done

echo "── 邊界：大小寫 / 只有名稱沒有 ASN 號 ──"
VPN_PRECHECK_FAKE_ORG="amazon technologies inc" bash "$SCRIPT" >/dev/null 2>&1
t "全小寫 amazon（無 AS 號）仍須命中" 10 $?
VPN_PRECHECK_FAKE_ORG="AS16509" bash "$SCRIPT" >/dev/null 2>&1
t "只有 AS 號、無公司名仍須命中" 10 $?

echo "── 邊界：不阻塞保證（查不到一律放行 exit 20）──"
mk_curl ""
PATH="$STUB:$PATH" bash "$SCRIPT" >/dev/null 2>&1
t "curl 回空（無網路/逾時）→ 放行不阻塞" 20 $?
mk_curl '{"ip":"1.2.3.4","city":"Taipei"}'
PATH="$STUB:$PATH" bash "$SCRIPT" >/dev/null 2>&1
t "回應缺 org 欄位 → 放行不阻塞" 20 $?
mk_curl 'not json at all'
PATH="$STUB:$PATH" bash "$SCRIPT" >/dev/null 2>&1
t "回應非 JSON → 放行不阻塞" 20 $?

echo "── 邊界：真實回應格式能被正確解析 ──"
mk_curl '{"ip":"3.113.244.170","city":"Tokyo","country":"JP","org":"AS16509 Amazon.com, Inc."}'
out=$(PATH="$STUB:$PATH" bash "$SCRIPT" 2>&1); rc=$?
t "真實 AWS 回應 → 命中" 10 $rc
case "$out" in
  *3.113.244.170*Tokyo*) PASS=$((PASS+1)); echo "  ✅ 診斷輸出含 ip 與地點" ;;
  *) FAIL=$((FAIL+1)); echo "  ❌ 診斷輸出缺 ip/地點: $out" ;;
esac
mk_curl '{"ip":"118.160.43.86","city":"Taipei","country":"TW","org":"AS3462 Data Communication Business Group"}'
PATH="$STUB:$PATH" bash "$SCRIPT" >/dev/null 2>&1
t "真實 HiNet 回應 → 放行" 0 $?

echo "── 接線：run-daily.sh 在 set -euo pipefail 下不被 exit 10 弄死 ──"
snippet=$(sed -n '/VPN_PRECHECK_START/,/VPN_PRECHECK_END/p' scripts/run-daily.sh)
if [ -z "$snippet" ]; then
  FAIL=$((FAIL+1)); echo "  ❌ run-daily.sh 找不到 VPN_PRECHECK_START/END marker（接線遺失）"
else
  PASS=$((PASS+1)); echo "  ✅ run-daily.sh 有 pre-check 接線 marker"
  SRC_PRECHECK=$(pwd)/scripts/vpn-precheck.sh
  run_snippet() {
    local fake="$1" tmp
    tmp=$(mktemp -d)
    mkdir -p "$tmp/scripts"; cp "$SRC_PRECHECK" "$tmp/scripts/"
    ( set -euo pipefail
      export REPO_DIR="$tmp" VPN_PRECHECK_FAKE_ORG="$fake" VPN_PRECHECK_NO_NOTIFY=1
      eval "$snippet" >/dev/null 2>&1
      touch "$tmp/.reached-end" )
    local rc=$?
    SNIPPET_TMP="$tmp"
    return $rc
  }

  run_snippet "AS16509 Amazon.com, Inc."
  t "set -e 下命中(CLOUD) 未中斷後續" 0 $?
  [ -f "$SNIPPET_TMP/.reached-end" ] && { PASS=$((PASS+1)); echo "  ✅ 命中後仍執行到 pre-check 段之後"; } || { FAIL=$((FAIL+1)); echo "  ❌ 命中後中斷、沒跑到後面"; }
  [ -f "$SNIPPET_TMP/ALERT-vpn-precheck.md" ] && { PASS=$((PASS+1)); echo "  ✅ 命中時產生 ALERT-vpn-precheck.md"; } || { FAIL=$((FAIL+1)); echo "  ❌ 命中卻沒產生 ALERT 檔"; }
  grep -q 'retry-failures' "$SNIPPET_TMP/ALERT-vpn-precheck.md" 2>/dev/null && { PASS=$((PASS+1)); echo "  ✅ ALERT 檔含補救指令"; } || { FAIL=$((FAIL+1)); echo "  ❌ ALERT 檔缺補救指令"; }
  rm -rf "$SNIPPET_TMP"

  run_snippet "AS3462 HiNet"
  t "set -e 下放行(OK) 未中斷後續" 0 $?
  [ -f "$SNIPPET_TMP/.reached-end" ] && { PASS=$((PASS+1)); echo "  ✅ 放行後仍執行到 pre-check 段之後"; } || { FAIL=$((FAIL+1)); echo "  ❌ 放行後中斷"; }
  rm -rf "$SNIPPET_TMP"

  tmp2=$(mktemp -d); mkdir -p "$tmp2/scripts"; cp "$SRC_PRECHECK" "$tmp2/scripts/"
  echo "殘留" > "$tmp2/ALERT-vpn-precheck.md"
  ( set -euo pipefail
    export REPO_DIR="$tmp2" VPN_PRECHECK_FAKE_ORG="AS3462 HiNet" VPN_PRECHECK_NO_NOTIFY=1
    eval "$snippet" >/dev/null 2>&1 )
  [ ! -f "$tmp2/ALERT-vpn-precheck.md" ] && { PASS=$((PASS+1)); echo "  ✅ 出口恢復正常時清掉舊 ALERT 檔"; } || { FAIL=$((FAIL+1)); echo "  ❌ 舊 ALERT 檔沒被清掉（會誤導）"; }
  rm -rf "$tmp2"
fi

echo
echo "══ PASS=$PASS FAIL=$FAIL ══"
[ "$FAIL" -eq 0 ]
