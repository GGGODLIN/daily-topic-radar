#!/bin/bash
# 出口 IP 雲端 ASN 偵測 — daily aggregator 的 pre-check
#
# 動機（2026-08-05 事故）：WireGuard 開著、預設路由走 utun4、出口 IP 3.113.244.170
# = AWS Tokyo（AS16509），reddit 5 個 sub 全 403，06:00 定版帶著 6 個 failure 就 commit，
# 直到 09:00 跑 digest 才發現。實查 07-22～08-05 共 15 天，「pipeline 一次到位」有
# 6/15（40%）不成立，其中 reddit 403 這一型佔 3/15。
# reddit 對雲端 IP（AWS/GCP/…）擋得比消費級 VPN exit 更死，住宅 IP 打 old.reddit HTML 仍 200。
#
# 設計取捨：
#   1. 偵測到**不擋跑** — 擋下等於當天零資料，比殘缺資料更糟。只讓問題在 06:00 就可見。
#   2. 判準是**出口 ASN 而非「有沒有 utun 介面」** — Tailscale 這類 split-tunnel 走 utun
#      但不影響對外出口，用介面判會誤殺。utun 只當診斷資訊附帶輸出。
#   3. **絕不阻塞** — 查不到 / 逾時 / 無網路一律 exit 20 放行。這道 gate 自己弄垮 daily run
#      的代價，遠大於它漏報一次。
#
# 退出碼：
#   0  出口正常（非雲端 ASN）
#   10 命中：出口是雲端 ASN
#   20 無法判定（無網路 / API 逾時 / 回應無法解析）→ 呼叫端應視為放行
#
# 測試：bash scripts/vpn-precheck.test.sh
# 強制走命中分支（不需真的開 VPN）：VPN_PRECHECK_FAKE_ORG="AS16509 Amazon.com, Inc." bash scripts/vpn-precheck.sh

CLOUD_ASN_PATTERN='AS16509|AS14618|amazon|aws|AS15169|AS396982|google|gcp|AS8075|AS8068|microsoft|azure|AS14061|digitalocean|AS63949|linode|AS20473|vultr|choopa|AS24940|hetzner|AS16276|ovh|AS31898|oracle|AS12876|scaleway|AS51167|contabo|AS9009|m247|AS60068|datacamp|AS212238|datapacket|AS9370|sakura|AS131965'

exit_iface=$(route -n get default 2>/dev/null | awk '/interface:/{print $2}')

if [ -n "${VPN_PRECHECK_FAKE_ORG:-}" ]; then
  org="$VPN_PRECHECK_FAKE_ORG"
  ip="(fake)"
  loc="(fake)"
else
  resp=$(curl -s --max-time 12 --connect-timeout 8 https://ipinfo.io/json 2>/dev/null)
  [ -z "$resp" ] && { echo "UNKNOWN: ipinfo 無回應（無網路或逾時）— 放行"; exit 20; }
  org=$(printf '%s' "$resp" | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin).get("org",""))' 2>/dev/null)
  ip=$(printf '%s' "$resp" | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin).get("ip",""))' 2>/dev/null)
  loc=$(printf '%s' "$resp" | /usr/bin/python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("city","?")+", "+d.get("country","?"))' 2>/dev/null)
  [ -z "$org" ] && { echo "UNKNOWN: ipinfo 回應無 org 欄位 — 放行"; exit 20; }
fi

if printf '%s' "$org" | grep -qiE "$CLOUD_ASN_PATTERN"; then
  echo "CLOUD: 出口是雲端 ASN — ip=$ip loc=$loc org=$org iface=${exit_iface:-?}"
  exit 10
fi

echo "OK: 出口非雲端 ASN — ip=$ip loc=$loc org=$org iface=${exit_iface:-?}"
exit 0
