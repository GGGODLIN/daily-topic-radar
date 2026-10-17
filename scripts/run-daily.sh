#!/bin/bash
cd /
set -euo pipefail

REPO_DIR="/Users/linhancheng/code/social-info"
UV_BIN="/Users/linhancheng/.local/bin/uv"
PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/linhancheng/.local/bin"
export PATH

cd "$REPO_DIR"

mkdir -p logs
DATE=$(date +%Y-%m-%d)
LOG_FILE="logs/cron-$DATE.log"

{
  echo "=== Daily run started: $(date) ==="

  if [ -f .env ]; then
    set -a
    . ./.env
    set +a
  fi

  export APIFY_RELAY_URL="${APIFY_RELAY_URL:-http://127.0.0.1:8788}"
  export APIFY_TOKEN_TWITTER=""
  export APIFY_TOKEN_THREADS=""

  # ── VPN_PRECHECK_START ─────────────────────────────────────────────────────
  # 出口雲端 ASN 偵測（2026-08-05 事故：WireGuard 開著、出口 AWS Tokyo AS16509，
  # reddit 5 個 sub 全 403，06:00 帶著 6 個 failure 定版，到 09:00 跑 digest 才發現）。
  # 刻意「偵測不擋跑」——擋下等於當天零資料，比殘缺更糟；目的只是讓問題在 06:00 就可見。
  # exit 10 = 命中（不是錯誤），故 `|| true` 吞掉，不讓 set -e 弄死整個 daily run。
  # 契約測試：bash scripts/vpn-precheck.test.sh（會 sed 抽出本段 START/END 之間 eval）
  VPN_PRECHECK_OUT=$(bash "$REPO_DIR/scripts/vpn-precheck.sh" 2>&1) || true
  case "$VPN_PRECHECK_OUT" in
    CLOUD:*)
      echo "=== ⚠️ VPN-PRECHECK 命中: $VPN_PRECHECK_OUT ==="
      echo "=== ⚠️ 出口是雲端 IP，reddit 5 個 sub 大機率整域 403。本次仍照跑；事後關掉 VPN 再跑 uv run python -m social_info --retry-failures 可補回 ==="
      cat > "$REPO_DIR/ALERT-vpn-precheck.md" <<ALERTEOF
# ⚠️ VPN pre-check 命中 — $(date '+%Y-%m-%d %H:%M:%S')

今晨 daily run 起跑時，對外出口是**雲端 ASN**，reddit 5 個 sub 大機率整域 403。

\`\`\`
$VPN_PRECHECK_OUT
\`\`\`

reddit 對雲端 IP（AWS / GCP / Azure …）擋得比消費級 VPN exit 更死；住宅 IP 打 old.reddit HTML 仍 200。

**補救**：關掉 VPN 後在 $REPO_DIR 跑

\`\`\`bash
uv run python -m social_info --retry-failures
\`\`\`

本檔每次 daily run 覆寫；出口恢復正常那天會自動刪除。
ALERTEOF
      [ -z "${VPN_PRECHECK_NO_NOTIFY:-}" ] && osascript -e "display notification \"出口是雲端 IP，reddit 大機率整域 403 — 關掉 VPN 後跑 --retry-failures 可補回\" with title \"⚠️ social-info VPN pre-check\" sound name \"Sosumi\"" 2>/dev/null || true
      ;;
    *)
      echo "=== VPN-PRECHECK: $VPN_PRECHECK_OUT ==="
      rm -f "$REPO_DIR/ALERT-vpn-precheck.md"
      ;;
  esac
  # ── VPN_PRECHECK_END ───────────────────────────────────────────────────────

  # hard-timeout 看門狗（2026-06-05 事故：aggregator 卡死 6h 零進展、無人察覺）。
  # 正常 10s–4min 完成；linux.do source budget 3000s，runner hard cap 3300s。
  # timeout → 砍 process tree（pkill -f social_info，底線 marker 不誤殺本 wrapper
  # 的 social-info 連字號路徑）+ log，不阻後續 commit 已產出 raw md。
  # 注意：這是止血。根因（某 fetcher sync blocking call block event loop / exit 階段不收尾）待 reproduce 深修。
  "$UV_BIN" run python -m social_info &
  AGG_PID=$!
  # watchdog 每 30s 檢查 aggregator 是否還活著：正常完成 → 自然退出（不殘留長 sleep）；
  # 110 輪 = 3300s 後仍活 → 砍 process tree。timeout 精度 ±30s，防 6h hang 足夠。
  ( for _ in $(seq 1 110); do
      sleep 30
      kill -0 "$AGG_PID" 2>/dev/null || exit 0
    done
    if kill -0 "$AGG_PID" 2>/dev/null; then
      echo "=== ⚠️ TIMEOUT: aggregator 超過 3300s 未結束，強制砍 process tree ==="
      kill -TERM "$AGG_PID" 2>/dev/null || true
      sleep 5
      pkill -KILL -f "social_info" 2>/dev/null || true
    fi ) &
  AGG_WD_PID=$!
  AGG_RC=0
  wait "$AGG_PID" || AGG_RC=$?
  kill "$AGG_WD_PID" 2>/dev/null || true
  if [ "$AGG_RC" -ne 0 ]; then
    echo "=== ⚠️ aggregator exit code ${AGG_RC}（被 timeout 砍或自身錯誤）；仍嘗試 commit 已產出 raw md ==="
  fi

  if [ -n "$(git status --porcelain state.db reports/)" ]; then
    git add state.db reports/
    git commit -m "chore: daily aggregate $DATE"
    # ── PUSH_GUARD_START ───────────────────────────────────────────────────────
    # push 失敗不是 silent：2026-10-17 GitHub push protection 擋下貼文裡的第三方 API key，
    # commit 留在本機一整天沒人知道。比照 VPN pre-check：log 一行＋ALERT 檔＋桌面通知，不擋後續。
    if git push; then
      rm -f "$REPO_DIR/ALERT-push-failed.md"
      echo "=== Pushed: $(git rev-parse HEAD) ==="
    else
      PUSH_HEAD=$(git rev-parse --short HEAD)
      echo "=== ⚠️ PUSH-FAILED: commit $PUSH_HEAD 留在本機、未上 GitHub；看上方 remote 訊息（GH013 = push protection 擋到貼文裡的 key）==="
      cat > "$REPO_DIR/ALERT-push-failed.md" <<ALERTEOF
# ⚠️ daily aggregate push 失敗 — $DATE

commit \`$PUSH_HEAD\` 已在本機、沒推上 GitHub。remote 回應見 \`logs/cron-$DATE.log\` 的 PUSH-FAILED 段。

- 看到 \`GH013\` / push protection：某篇貼文含看起來像 API key 的字串。ingest 層的遮罩（src/social_info/redact.py）沒認出這個格式——補 pattern 後，把 raw md 與 state.db 裡那串換成 \`[REDACTED-KEY]\`，\`git reset --soft origin/main\` 重提再 push。不要用 GitHub 的 allow-secret 連結（那是把別人洩漏的 key 再公開一次）。
- 其他原因（網路、認證、檔案過大）：修好後 \`git push\` 即可，下次成功 push 會自動刪本檔。
ALERTEOF
      [ -z "${PUSH_GUARD_NO_NOTIFY:-}" ] && osascript -e "display notification \"commit $PUSH_HEAD 留在本機；看 ALERT-push-failed.md\" with title \"⚠️ social-info push 失敗\" sound name \"Sosumi\"" 2>/dev/null || true
    fi
    # ── PUSH_GUARD_END ─────────────────────────────────────────────────────────
  else
    echo "=== No changes to commit ==="
  fi

  echo "=== Daily run finished: $(date) ==="
} >> "$LOG_FILE" 2>&1
