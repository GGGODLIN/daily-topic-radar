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

  "$UV_BIN" run python -m social_info

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
