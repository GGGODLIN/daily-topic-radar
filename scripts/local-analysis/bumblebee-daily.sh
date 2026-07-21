#!/bin/bash
cd /
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/Users/linhancheng/.local/bin"
export PATH

REPO_DIR="/Users/linhancheng/code/social-info"
VENDOR_DIR="$REPO_DIR/vendor/bumblebee"
BIN="$VENDOR_DIR/bin/bumblebee"
CATALOG_REPO="$VENDOR_DIR/catalog"
CATALOG_DIR="$CATALOG_REPO/threat_intel"
ARCHIVE_DIR="$VENDOR_DIR/archive"
OUT_DIR="$REPO_DIR/reports/local-analysis"
LOG_DIR="$REPO_DIR/logs"

mkdir -p "$OUT_DIR" "$LOG_DIR" "$ARCHIVE_DIR"
DATE=$(date +%Y-%m-%d)
OUT="$OUT_DIR/$DATE-bumblebee.md"
LOG="$LOG_DIR/local-analysis-bumblebee-$DATE.log"
FINDINGS_FILE="$ARCHIVE_DIR/$DATE-findings.ndjson"

log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG"; }

log "═══ bumblebee daily-scan start ($DATE) ═══"

if [ ! -x "$BIN" ]; then
  log "ERROR: bumblebee binary missing at $BIN"
  exit 1
fi

# 1. Pull latest catalog
# ⚠️ 2026-07-17 起 pin 在 d753592（最後一個 schema 0.1.0 commit）：
#    upstream 07-16 起 catalog 全面 bump schema_version 0.2.0，本機 binary v0.1.1（latest release
#    v0.1.2 亦早於 bump）只支援 0.1.0，會整包拒讀、scan 零覆蓋。
#    解除條件：upstream 出支援 0.2.0 的 release → 換 bin/bumblebee → 刪 CATALOG_PIN 恢復 track main。
CATALOG_PIN="d75359246a6be8a6a8fcaca089ef2dabcf5d75fe"
PREV_HEAD="$(git -C "$CATALOG_REPO" rev-parse HEAD)"
NEW_HEAD="$PREV_HEAD"
UPSTREAM_HEAD=""
if git -C "$CATALOG_REPO" fetch --quiet origin main && \
   git -C "$CATALOG_REPO" reset --hard --quiet "$CATALOG_PIN"; then
  NEW_HEAD="$(git -C "$CATALOG_REPO" rev-parse HEAD)"
  UPSTREAM_HEAD="$(git -C "$CATALOG_REPO" rev-parse --short origin/main)"
  if [ "$PREV_HEAD" != "$NEW_HEAD" ]; then
    log "catalog moved to pin: $(echo $PREV_HEAD | cut -c1-7) -> $(echo $NEW_HEAD | cut -c1-7) (upstream main=$UPSTREAM_HEAD)"
  else
    log "catalog pinned (HEAD=$(echo $NEW_HEAD | cut -c1-7), upstream main=$UPSTREAM_HEAD)"
  fi
else
  log "WARN: git pull failed, using stale catalog"
fi

# 2. Snapshot catalog entry count
ENTRY_COUNT=$(find "$CATALOG_DIR" -name '*.json' -exec jq '.entries | length' {} \; \
  | awk '{s+=$1} END {print s}')
log "catalog entries total: $ENTRY_COUNT"

# 3. Run baseline scan with catalog (findings-only)
SCAN_START=$(date +%s)
if "$BIN" scan \
    --profile baseline \
    --exposure-catalog "$CATALOG_DIR" \
    --findings-only \
    --output file --output-file "$FINDINGS_FILE" \
    2>>"$LOG"; then
  SCAN_RC=0
else
  SCAN_RC=$?
fi
SCAN_DUR=$(( $(date +%s) - SCAN_START ))

# 4a. Loud fail：scan 非 0 退出或 findings 檔沒產生 → 醒目報告 + 桌面通知（2026-07-17 加，
#     防當日 schema mismatch 造成 silent "?" 假乾淨、digest 誤讀 findings=0）
if [ "$SCAN_RC" -ne 0 ] || [ ! -s "$FINDINGS_FILE" ]; then
  {
    echo "# Bumblebee daily scan — $DATE"
    echo ""
    echo "## 🚨 SCAN FAILED — 今日供應鏈掃描零覆蓋"
    echo ""
    echo "- exit code: \`$SCAN_RC\`；findings 檔 $([ -f "$FINDINGS_FILE" ] && echo '為空' || echo '未產生')"
    echo "- log tail："
    echo '```'
    tail -5 "$LOG"
    echo '```'
    echo ""
    echo "⚠️ findings=0 不可信（掃描未執行）。先查 binary 與 catalog schema 相容性再信結果。"
  } > "$OUT"
  osascript -e "display notification \"Bumblebee scan FAILED (exit $SCAN_RC) — 今日零掃描覆蓋\" with title \"🚨 Bumblebee scan failed\" sound name \"Sosumi\"" 2>/dev/null || true
  log "SCAN FAILED: loud report written (rc=$SCAN_RC)"
  exit 0
fi

# 4. Parse summary
FINDING_COUNT=$(jq -s '[.[] | select(.record_type=="finding")] | length' "$FINDINGS_FILE" 2>/dev/null || echo "?")
PKG_COUNT=$(jq -s '.[] | select(.record_type=="scan_summary") | .package_records_suppressed' "$FINDINGS_FILE" 2>/dev/null || echo "?")
STATUS=$(jq -s '.[] | select(.record_type=="scan_summary") | .status' "$FINDINGS_FILE" 2>/dev/null | tr -d '"' || echo "?")

log "scan complete: status=$STATUS findings=$FINDING_COUNT pkgs=$PKG_COUNT duration=${SCAN_DUR}s exit=$SCAN_RC"

# 5. Write daily review markdown to reports/local-analysis/
CATALOG_HEAD="$(echo $NEW_HEAD | cut -c1-7)"
{
  echo "# Bumblebee daily scan — $DATE"
  echo ""
  echo "| 項目 | 值 |"
  echo "|---|---|"
  echo "| Status | \`$STATUS\` |"
  echo "| Scan 時長 | ${SCAN_DUR}s |"
  echo "| Catalog HEAD | \`$CATALOG_HEAD\` |"
  echo "| Catalog entries | $ENTRY_COUNT |"
  echo "| Packages 掃過 | $PKG_COUNT |"
  echo "| **Findings** | **$FINDING_COUNT** |"
  echo ""
  echo "⚠️ Catalog pinned at \`$CATALOG_HEAD\`（binary v0.1.1 不支援 upstream schema 0.2.0；upstream main=\`${UPSTREAM_HEAD:-?}\`）——新 threat entries 暫停進場，upstream release 支援 0.2.0 後換 binary 解除 pin。"
  echo ""
  if [ "$PREV_HEAD" != "$NEW_HEAD" ]; then
    echo "## Catalog 變動"
    echo ""
    echo "上次 HEAD: \`$(echo $PREV_HEAD | cut -c1-7)\` → 今天 HEAD: \`$CATALOG_HEAD\`"
    echo ""
    echo "\`\`\`"
    git -C "$CATALOG_REPO" log --oneline "$PREV_HEAD..$NEW_HEAD" -- threat_intel/ 2>/dev/null || echo "(無 threat_intel/ 變動)"
    echo "\`\`\`"
    echo ""
  fi
  if [ "$FINDING_COUNT" != "0" ] && [ "$FINDING_COUNT" != "?" ]; then
    echo "## ⚠️ Findings"
    echo ""
    jq -s -r '.[] | select(.record_type=="finding") | "- **\(.package_name)@\(.version)** (\(.ecosystem))  \n  catalog: \(.catalog_name) / id=\(.catalog_id)  \n  source: \(.source_file)  \n  severity: \(.severity)\n"' "$FINDINGS_FILE"
    echo ""
    echo "完整 NDJSON：\`vendor/bumblebee/archive/$DATE-findings.ndjson\`"
  else
    echo "## 結果"
    echo ""
    echo "今天無命中。全部 catalog entries ($ENTRY_COUNT) 都跟你 $PKG_COUNT 個本機 package 對過、無 exact \`(ecosystem, name, version)\` match。"
  fi
} > "$OUT"

# 6. Alert: findings > 0 → desktop notification + ALERT.md to repo root
if [ "$FINDING_COUNT" != "0" ] && [ "$FINDING_COUNT" != "?" ]; then
  log "ALERT: $FINDING_COUNT findings detected"
  cp "$OUT" "$REPO_DIR/ALERT-bumblebee.md"
  osascript -e "display notification \"Bumblebee found $FINDING_COUNT supply-chain match(es) — see $OUT\" with title \"⚠️ Bumblebee Alert\" sound name \"Sosumi\"" 2>/dev/null || true
fi

# 7. Prune archive findings.ndjson > 30 days
find "$ARCHIVE_DIR" -name '*-findings.ndjson' -mtime +30 -delete 2>/dev/null || true

log "═══ bumblebee daily-scan done ═══"
exit 0
