#!/bin/bash
cd /
set -euo pipefail

REPO_DIR="/Users/linhancheng/code/social-info"
UV_BIN="/Users/linhancheng/.local/bin/uv"
PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/linhancheng/.local/bin"
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

  # hard-timeout 看門狗（2026-06-05 事故：aggregator 卡死 6h 零進展、無人察覺）。
  # 正常 10s–4min 完成；給 10min hard cap。timeout → 砍 process tree（pkill -f social_info，
  # 底線 marker 不誤殺本 wrapper 的 social-info 連字號路徑）+ log，不阻後續 commit 已產出 raw md。
  # 注意：這是止血。根因（某 fetcher sync blocking call block event loop / exit 階段不收尾）待 reproduce 深修。
  "$UV_BIN" run python -m social_info &
  AGG_PID=$!
  # watchdog 每 30s 檢查 aggregator 是否還活著：正常完成 → 自然退出（不殘留長 sleep）；
  # 20 輪 = 600s（10min）後仍活 → 砍 process tree。timeout 精度 ±30s，防 6h hang 足夠。
  ( for _ in $(seq 1 20); do
      sleep 30
      kill -0 "$AGG_PID" 2>/dev/null || exit 0
    done
    if kill -0 "$AGG_PID" 2>/dev/null; then
      echo "=== ⚠️ TIMEOUT: aggregator 超過 600s 未結束，強制砍 process tree ==="
      kill -TERM "$AGG_PID" 2>/dev/null || true
      sleep 5
      pkill -KILL -f "social_info" 2>/dev/null || true
    fi ) &
  AGG_WD_PID=$!
  AGG_RC=0
  wait "$AGG_PID" || AGG_RC=$?
  kill "$AGG_WD_PID" 2>/dev/null || true
  if [ "$AGG_RC" -ne 0 ]; then
    echo "=== ⚠️ aggregator exit code $AGG_RC（被 timeout 砍或自身錯誤）；仍嘗試 commit 已產出 raw md ==="
  fi

  if [ -n "$(git status --porcelain state.db reports/)" ]; then
    git add state.db reports/
    git commit -m "chore: daily aggregate $DATE"
    git push
    echo "=== Pushed: $(git rev-parse HEAD) ==="
  else
    echo "=== No changes to commit ==="
  fi

  echo "=== Daily run finished: $(date) ==="
} >> "$LOG_FILE" 2>&1
