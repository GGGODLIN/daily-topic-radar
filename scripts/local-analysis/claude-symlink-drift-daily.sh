#!/bin/bash
cd /
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/Users/linhancheng/.local/bin"
export PATH

REPO_DIR="/Users/linhancheng/code/social-info"
OUT_DIR="$REPO_DIR/reports/local-analysis"
LOG_DIR="$REPO_DIR/logs"
DATE="${LOCAL_ANALYSIS_DATE:-$(date +%Y-%m-%d)}"
OUT="$OUT_DIR/$DATE-symlink-drift.md"
LOG="$LOG_DIR/local-analysis-symlink-drift-$DATE.log"
mkdir -p "$OUT_DIR" "$LOG_DIR"

log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG"; }

CLAUDE_ROOT="$HOME/.claude"
LOCK_DIRS=("$HOME/.claude-team" "$HOME/.claude-max")

is_whitelisted() {
  local name="$1"
  case "$name" in
    .claude.json|backups|policy-limits.json|.DS_Store|.last-update-result.json) return 0 ;;
    .claude.json.backup.*) return 0 ;;
  esac
  return 1
}

log "═══ claude symlink drift check ($DATE) ═══"

DRIFT_COUNT=0
LOCAL_ONLY_COUNT=0
DRIFT_LINES=""
LOCAL_ONLY_LINES=""

for lock in "${LOCK_DIRS[@]}"; do
  if [[ ! -d "$lock" ]]; then
    log "WARN: $lock missing — skip"
    continue
  fi
  log "scanning $lock"

  shopt -s nullglob dotglob
  for entry in "$lock"/*; do
    name=$(basename "$entry")
    [[ "$name" == "." || "$name" == ".." ]] && continue

    if is_whitelisted "$name"; then
      continue
    fi

    default_entry="$CLAUDE_ROOT/$name"

    if [[ ! -e "$default_entry" && ! -L "$default_entry" ]]; then
      LOCAL_ONLY_COUNT=$((LOCAL_ONLY_COUNT + 1))
      LOCAL_ONLY_LINES+=$'\n'"- \`$entry\` — lock-dir only（default 無對應）"
      continue
    fi

    if [[ ! -L "$entry" ]]; then
      DRIFT_COUNT=$((DRIFT_COUNT + 1))
      kind="file"
      [[ -d "$entry" ]] && kind="dir"
      DRIFT_LINES+=$'\n'"- \`$entry\` ($kind) — default has \`$default_entry\`"
      log "DRIFT: $entry"
    fi
  done
  shopt -u nullglob dotglob
done

{
  echo "# Claude symlink drift report ($DATE)"
  echo ""
  echo "**目標**：偵測 \`~/.claude-team\` / \`~/.claude-max\` 內被 claude binary atomic write 拆 symlink 變 real file/dir 的 entries。"
  echo ""
  echo "**Whitelist（不算 drift）**：\`.claude.json\` / \`.claude.json.backup.*\` / \`backups/\` / \`policy-limits.json\` / \`.DS_Store\` / \`.last-update-result.json\`（updater ephemeral state、atomic write 必拆 symlink，2026-07-31 拍板）"
  echo ""
  echo "## 結果"
  echo ""
  echo "- Drift count: **${DRIFT_COUNT}**"
  echo "- Lock-only count: ${LOCAL_ONLY_COUNT}（默默存在的 lock-dir-only entries，不一定是 bug）"

  if (( DRIFT_COUNT > 0 )); then
    echo ""
    echo "## Drift 清單（要重建 symlink）"
    printf '%s' "$DRIFT_LINES"
    echo ""
    echo ""
    echo "## 修復指令"
    echo ""
    echo '```bash'
    echo 'for lock in $HOME/.claude-team $HOME/.claude-max; do'
    echo '  cd "$lock"'
    echo '  shopt -s nullglob dotglob 2>/dev/null'
    echo '  for name in *; do'
    echo '    case "$name" in'
    echo '      .claude.json|.claude.json.backup.*|backups|policy-limits.json|.DS_Store|.last-update-result.json) continue ;;'
    echo '    esac'
    echo '    [[ -L "$name" ]] && continue'
    echo '    [[ -e "$HOME/.claude/$name" ]] || continue'
    echo '    rm -rf "$name"'
    echo '    ln -s "$HOME/.claude/$name" "$name"'
    echo '  done'
    echo 'done'
    echo '```'
  else
    echo ""
    echo "✅ 無 drift。三個 dir symlink layer 對齊。"
  fi

  if (( LOCAL_ONLY_COUNT > 0 )); then
    echo ""
    echo "## Lock-only entries（資訊）"
    printf '%s' "$LOCAL_ONLY_LINES"
    echo ""
  fi
} > "$OUT"

log "═══ done. drift=$DRIFT_COUNT  local-only=$LOCAL_ONLY_COUNT  report=$OUT ═══"
