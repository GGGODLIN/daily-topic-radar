#!/bin/bash
# 每週公開 harness 發布候選 channel（openspec publish-harness-governance 第 09 票）。
# 手動提早跑：直接 `bash publish-candidate-weekly.sh`（可帶 LOCAL_ANALYSIS_DATE）；排程由 local-analysis.js 的 publish-candidate（weekly-tue）經 run-shell-channel.sh 呼叫。
#
# 跨檔字串依賴：下方兩個 heading（H_CAND、H_FAIL）必須與
# ~/.claude/hooks/daily-local-analysis-trigger.sh 排檔條款裡的字串逐字一致；
# 兩邊任一改字，候選就不會被排進 digest 的 🔴 高檔（hooks/daily-local-analysis-trigger.test.sh 與本檔的 test 各鎖一邊，
# 本檔 test 另會直接讀 hook 注入文字比對）。
#
# 威脅模型：這支 channel 只「產生並呈報」候選，從不 push、不同步公開 repo（匯出一律 --dry-run）、不啟動全審。
# 防的是誠實但健忘的 agent 把候選當成可自行執行的待辦；不防刻意繞過的 agent——真正擋 push 的是 public-push-gate hook（第 10 票）。
# 第 3 關是模型審查，品質機率性：它漏掉的東西，靠第 1、2 關與使用者決策兜底；判不出來一律當「未完成」，不當通過。
#
# 模型呼叫路由與 recap-daily.sh 的 run_recap 同一套（原生 Claude 用 native Opus、其餘啟動方式走本機 CLIProxyAPI 的 Luna(max)）；那邊改路由，這邊要跟著改。
# 以下 PUBLISH_CANDIDATE_* 覆寫只給測試與手動把路徑導到別處用。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd /
set -uo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/linhancheng/.local/bin:/opt/homebrew/bin"
export PATH

PARENT_CC_VENDOR="${CC_VENDOR-}"
PARENT_ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL-}"

HOME_DIR="${PUBLISH_CANDIDATE_HOME:-$HOME}"
REPO_DIR="${PUBLISH_CANDIDATE_REPO_DIR:-/Users/linhancheng/code/social-info}"
EXPORT_SCRIPT="${PUBLISH_CANDIDATE_EXPORT_SCRIPT:-$HOME_DIR/.claude/scripts/harness-export/export.mjs}"
CONFIG_DIR="${PUBLISH_CANDIDATE_CONFIG_DIR:-$HOME_DIR/.claude/scripts/harness-export/private}"
PUBLIC_DIR="${PUBLISH_CANDIDATE_PUBLIC_DIR:-$HOME_DIR/Desktop/projects/claude-harness-public}"
NODE_BIN="${PUBLISH_CANDIDATE_NODE:-/opt/homebrew/bin/node}"
CLAUDE="${PUBLISH_CANDIDATE_CLAUDE_BIN:-/Users/linhancheng/.local/bin/claude}"
KEYS_FILE="${PUBLISH_CANDIDATE_KEYS_FILE:-$HOME/.cli-proxy-api/keys.env}"
GPT_MODELS_FILE="${PUBLISH_CANDIDATE_GPT_MODELS_FILE:-$HOME/.claude/config/gpt-models.env}"
# 上限 300 秒：local-analysis 以 haiku 代理跑 shell channel，Bash 工具單次上限 10 分鐘。
REVIEW_TIMEOUT="${PUBLISH_CANDIDATE_REVIEW_TIMEOUT:-300}"
MAX_DIFF_BYTES="${PUBLISH_CANDIDATE_MAX_DIFF_BYTES:-60000}"
MAX_NEW_ITEMS="${PUBLISH_CANDIDATE_MAX_NEW_ITEMS:-40}"

OUT_DIR="$REPO_DIR/reports/local-analysis"
LOG_DIR="$REPO_DIR/logs"
mkdir -p "$OUT_DIR" "$LOG_DIR"
DATE="${LOCAL_ANALYSIS_DATE:-$(date +%Y-%m-%d)}"
OUT="$OUT_DIR/$DATE-publish-candidate.md"
LOG="$LOG_DIR/local-analysis-publish-candidate-$DATE.log"

H_CAND='## 📦 公開 harness 發布候選（需要你決策，agent 不得代做）'
H_FAIL='## ⚠️ 公開 harness 發布候選檢查失敗'
NO_CANDIDATE='本週沒有發布候選'
ACCEPTED_PATH='skills/mutation-testing'

TMP="$(mktemp -d)"
trap 'rc=$?; rm -rf "$TMP"; exit $rc' EXIT

# ---- helpers ---------------------------------------------------------------

# 同日重跑：run-shell-channel.sh 會先把既有報告搬到 .previous，所以兩處都要找。
# 候選段永遠是報告最後一段，從 heading 取到檔尾就是完整的候選。
extract_prior_candidate() {
  local f
  for f in "$OUT" "$OUT.previous"; do
    [ -s "$f" ] || continue
    if grep -qxF -- "$H_CAND" "$f"; then
      awk -v h="$H_CAND" '$0 == h { p = 1 } p' "$f"
      return 0
    fi
  done
  return 0
}

write_report() { # write_report <file> — 以暫存檔換入，避免被中斷時留下半份報告
  local src="$1"
  mv "$src" "$OUT"
}

# 失敗報告：🔴 顯示失敗，不產生候選；同日稍早的候選原樣保留在最後一段。
finish_failure() { # finish_failure <原因行> [<細節檔>]
  local reason="$1" detail="${2:-}" f="$TMP/report.out"
  {
    echo "# 公開 harness 每週發布候選 — $DATE"
    echo ""
    echo "$H_FAIL"
    echo ""
    echo "⚠️ $reason"
    if [ -n "$detail" ] && [ -s "$detail" ]; then
      echo ""
      echo '```'
      head -n 15 "$detail"
      echo '```'
    fi
    echo ""
    echo "本次沒有產生發布候選；這不代表可以發布，要等失敗原因排除、下一輪重跑才會有候選。"
    if [ -s "$PRIOR" ]; then
      echo ""
      echo "⚠️ 同日稍早產生的候選（見它的「產生時間」）仍保留在下方；本次重跑失敗，沒有重新驗證它，請先確認它是否過期。"
      echo ""
      cat "$PRIOR"
    fi
  } > "$f"
  write_report "$f"
  echo "failure: $reason → report written"
}

with_timeout() { # alarm 在 exec 後仍有效；逾時以 SIGALRM 結束（退出碼 142）
  perl -e 'alarm shift @ARGV; exec @ARGV or exit 127' "$REVIEW_TIMEOUT" "$@"
}

run_review() { # stdin = prompt；stdout = 審查結果
  local luna_model=''
  if [[ -z "$PARENT_CC_VENDOR" && -z "$PARENT_ANTHROPIC_BASE_URL" ]]; then
    (
      unset ANTHROPIC_MODEL ANTHROPIC_DEFAULT_FABLE_MODEL ANTHROPIC_DEFAULT_OPUS_MODEL
      unset ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_HAIKU_MODEL
      unset CLAUDE_CODE_SUBAGENT_MODEL ANTHROPIC_AUTH_TOKEN
      export CC_VENDOR=headless-channel
      with_timeout "$CLAUDE" --model opus -p
    )
    return $?
  fi
  if [[ ! -f "$KEYS_FILE" ]]; then
    printf 'review route: keys file missing: %s\n' "$KEYS_FILE" >&2
    return 1
  fi
  if [[ -f "$GPT_MODELS_FILE" ]]; then
    local key value
    while IFS='=' read -r key value; do
      [[ "$key" == GPT_LUNA ]] && luna_model="${value%$'\r'}(max)"
    done < "$GPT_MODELS_FILE"
  fi
  if [[ -z "$luna_model" ]]; then
    printf 'review route: GPT_LUNA missing from %s\n' "$GPT_MODELS_FILE" >&2
    return 1
  fi
  (
    local CLIPROXY_BASE_URL='' CLIPROXY_KEY_CC='' key value
    while IFS='=' read -r key value; do
      value="${value%$'\r'}"
      case "$key" in
        CLIPROXY_BASE_URL) CLIPROXY_BASE_URL="$value" ;;
        CLIPROXY_KEY_CC) CLIPROXY_KEY_CC="$value" ;;
      esac
    done < "$KEYS_FILE"
    if [[ -z "$CLIPROXY_BASE_URL" || -z "$CLIPROXY_KEY_CC" ]]; then
      printf 'review route: keys file must define CLIPROXY_BASE_URL and CLIPROXY_KEY_CC\n' >&2
      exit 1
    fi
    case "$CLIPROXY_BASE_URL" in
      http://127.0.0.1:8317|http://localhost:8317) ;;
      *) printf 'review route: relay URL must be local port 8317\n' >&2; exit 1 ;;
    esac
    unset ANTHROPIC_API_KEY ANTHROPIC_FALLBACK_MODEL CLAUDE_CODE_FALLBACK_MODEL
    export CC_VENDOR=headless-channel
    export ANTHROPIC_BASE_URL="$CLIPROXY_BASE_URL"
    export ANTHROPIC_AUTH_TOKEN="$CLIPROXY_KEY_CC"
    export ANTHROPIC_MODEL="$luna_model"
    export ANTHROPIC_DEFAULT_FABLE_MODEL="$luna_model"
    export ANTHROPIC_DEFAULT_OPUS_MODEL="$luna_model"
    export ANTHROPIC_DEFAULT_SONNET_MODEL="$luna_model"
    export ANTHROPIC_DEFAULT_HAIKU_MODEL="$luna_model"
    export CLAUDE_CODE_SUBAGENT_MODEL="$luna_model"
    export CLAUDE_CODE_MAX_CONTEXT_TOKENS="${CLAUDE_CODE_MAX_CONTEXT_TOKENS:-1000000}"
    export CLAUDE_CODE_AUTO_COMPACT_WINDOW="${CLAUDE_CODE_AUTO_COMPACT_WINDOW:-900000}"
    export API_TIMEOUT_MS="${API_TIMEOUT_MS:-3000000}"
    export CLAUDE_STREAM_IDLE_TIMEOUT_MS="${CLAUDE_STREAM_IDLE_TIMEOUT_MS:-600000}"
    export CLAUDE_BYTE_STREAM_IDLE_TIMEOUT_MS="${CLAUDE_BYTE_STREAM_IDLE_TIMEOUT_MS:-600000}"
    with_timeout "$CLAUDE" --model "$luna_model" -p
  )
}

# 上次發布之後有變動的設定檔（白名單、替換規則與刪段、接線點清單）。判斷依據是 git 歷史與工作樹，不是內容比對。
# 輸出：每行一個檔名；回傳 2 表示判斷不了（設定不在 git 追蹤內）。
changed_config_files() {
  local f tracked=""
  for f in allowlist.json replace-rules.json deletions.json wiring.json; do
    [ -e "$CONFIG_DIR/$f" ] && tracked="$tracked $f"
  done
  [ -n "$tracked" ] || return 2
  git -C "$CONFIG_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 2
  # shellcheck disable=SC2086
  [ -n "$(git -C "$CONFIG_DIR" ls-files -- $tracked 2>/dev/null)" ] || return 2
  {
    # shellcheck disable=SC2086
    git -C "$CONFIG_DIR" log --since="$PUB_TS" --name-only --format= -- $tracked
    # shellcheck disable=SC2086
    git -C "$CONFIG_DIR" status --porcelain -- $tracked | sed 's/^...//'
  } | sed 's|.*/||' | sort -u | sed '/^$/d'
  return 0
}

# 白名單之外、上次發布後新出現的項目（絕對路徑，每行一個）。
# 根目錄 = 白名單每個條目來源的上一層；只有該根目錄底下有「目錄條目」時才把子目錄算項目，否則只算直屬檔案，
# 不然 ~/.claude 頂層每週新增的 memory／sessions 目錄都會被當成新元件。
list_new_items() {
  local allow="$CONFIG_DIR/allowlist.json" root seg abs allow_dirs d covered
  jq -r '.entries[].source' "$allow" 2>/dev/null | sed "s|^~/|$HOME_DIR/|" > "$TMP/sources" || return 1
  : > "$TMP/dirsrc"; : > "$TMP/roots"; : > "$TMP/dirroots"
  while IFS= read -r abs; do
    [ -n "$abs" ] || continue
    dirname "$abs" >> "$TMP/roots"
    if [ -d "$abs" ]; then echo "$abs" >> "$TMP/dirsrc"; dirname "$abs" >> "$TMP/dirroots"; fi
  done < "$TMP/sources"
  sort -u "$TMP/roots" -o "$TMP/roots"
  : > "$TMP/cand"
  while IFS= read -r root; do
    [ -d "$root" ] || continue
    git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1 || continue
    allow_dirs=0; grep -qxF -- "$root" "$TMP/dirroots" && allow_dirs=1
    {
      git -C "$root" log --since="$PUB_TS" --diff-filter=A --name-only --relative --format= -- . 2>/dev/null
      git -C "$root" ls-files --others --exclude-standard -- . 2>/dev/null
    } | awk -F/ 'NF { print $1 }' | sort -u | while IFS= read -r seg; do
      abs="$root/$seg"
      [ -e "$abs" ] || continue
      if [ -d "$abs" ] && [ "$allow_dirs" -eq 0 ]; then continue; fi
      echo "$abs"
    done >> "$TMP/cand"
  done < "$TMP/roots"
  sort -u "$TMP/cand" | while IFS= read -r abs; do
    grep -qxF -- "$abs" "$TMP/sources" && continue
    covered=0
    while IFS= read -r d; do
      case "$abs" in "$d"/*) covered=1; break ;; esac
    done < "$TMP/dirsrc"
    [ "$covered" -eq 0 ] && echo "$abs"
  done
  return 0
}

display_path() { printf '%s' "$1" | sed "s|^$HOME_DIR/|~/|"; }

build_prompt() { # build_prompt <new items 檔> → stdout
  cat <<'EOF'
你是公開前的差異審查員。以下是「私人 harness 準備公開的內容」與「公開 repo 目前 HEAD」的差異。機械檢查（敏感字掃描、接線檢查）已經通過，所以你要找的是字串比對抓不到的東西：
- 能辨識個人或公司身分的內容（公司、客戶、同事、內部專案代號、內部網域或端點、私人 email、只有本人才懂的路徑與帳號脈絡）
- 看起來不該公開的設定、憑證形狀、內部流程細節
- 上游授權或出處問題

規則：
1. 只讀本訊息內的資料，不要呼叫任何工具，不要修改任何東西。
2. `skills/mutation-testing` 的上游翻譯／照搬段落（snava10/alist、hughescr/claude-code-config、otomatty/zedi）是使用者已拍板照原樣公開的已知風險（ADR 0004）：不要把它們列為發現。
3. 沒把握就誠實回報 low-confidence，不要猜成 clean。
4. 下方「新增項目」是白名單之外、上次發布後新出現的檔案或目錄；逐項判斷該不該收進公開版（include）或不收（exclude），並寫一句理由。

輸出格式（逐行，不要加其他說明）：
GATE3_RESULT: clean | findings | low-confidence
FINDING: <公開路徑>:<行號或 -> | <一句話描述問題>      （每個發現一行；沒有就不寫）
NEW_FILE: <新增項目的路徑，照抄下方> | include 或 exclude | <一句理由>      （每個新增項目一行）
EOF
  echo ""
  echo "===== 差異（unified diff，head/ = 公開 HEAD，stage/ = 本次匯出）====="
  head -c "$MAX_DIFF_BYTES" "$TMP/full.diff"
  if [ "$DIFF_TRUNCATED" -eq 1 ]; then
    echo ""
    echo "[差異超過 $MAX_DIFF_BYTES bytes 已截斷；你只看到前段，請依此調整信心]"
  fi
  echo ""
  echo "===== 新增項目 ====="
  if [ ! -s "$1" ]; then
    echo "（無）"
  else
    while IFS= read -r abs; do
      if [ -d "$abs" ]; then
        echo "--- $(display_path "$abs")/ （目錄）檔案："
        find "$abs" -type f -not -path '*/.git/*' 2>/dev/null | sed "s|^$abs/||" | sort | head -n 15
        local lead
        for lead in SKILL.md README.md; do
          if [ -f "$abs/$lead" ]; then echo "[$lead 開頭]"; head -c 1500 "$abs/$lead"; echo ""; break; fi
        done
      else
        echo "--- $(display_path "$abs") （檔案）開頭："
        head -c 1500 "$abs"
        echo ""
      fi
    done < "$1"
  fi
}

one_line() { tr '\r\n' '  ' | sed 's/  */ /g; s/^ //; s/ $//'; }

# ---- main ------------------------------------------------------------------

{
  echo "=== publish-candidate weekly started: $(date) ==="

  PRIOR="$TMP/prior.md"
  extract_prior_candidate > "$PRIOR"

  if [ ! -d "$PUBLIC_DIR" ] || ! git -C "$PUBLIC_DIR" rev-parse --verify --quiet HEAD >/dev/null 2>&1; then
    finish_failure "公開 repo 本機副本讀不到 HEAD：$PUBLIC_DIR" ""
    echo "=== publish-candidate weekly finished: $(date) ==="; exit 0
  fi
  PUB_SHA="$(git -C "$PUBLIC_DIR" log -1 --format=%h)"
  PUB_SUBJECT="$(git -C "$PUBLIC_DIR" log -1 --format=%s)"
  PUB_TS="$(git -C "$PUBLIC_DIR" log -1 --format=%cI)"

  # 1. 匯出（只產生、不同步）
  STAGE="$TMP/stage"
  GATE_REPORT="$TMP/gates.json"
  EXPORT_ERR="$TMP/export.err"
  "$NODE_BIN" "$EXPORT_SCRIPT" --config-dir "$CONFIG_DIR" --dry-run --stage-dir "$STAGE" --report "$GATE_REPORT" --home "$HOME_DIR" > "$TMP/export.out" 2> "$EXPORT_ERR"
  EXPORT_RC=$?
  if [ "$EXPORT_RC" -ne 0 ]; then
    finish_failure "匯出或機械關卡失敗（匯出結束碼 ${EXPORT_RC}）" "$EXPORT_ERR"
    echo "=== publish-candidate weekly finished: $(date) ==="; exit 0
  fi
  G1="$(jq -r '.gate1.status // "?"' "$GATE_REPORT" 2>/dev/null || echo "?")"
  G2="$(jq -r '.gate2.status // "?"' "$GATE_REPORT" 2>/dev/null || echo "?")"
  if [ "$G1" != passed ] || [ "$G2" != passed ]; then
    # 結束碼 0 卻讀不到「通過」：報告缺失或格式不符都當未通過，不當零命中。
    finish_failure "兩關機械檢查結果讀不到或未通過（第 1 關：${G1}；第 2 關：${G2}）" "$EXPORT_ERR"
    echo "=== publish-candidate weekly finished: $(date) ==="; exit 0
  fi
  G1_FILES="$(jq -r '.gate1.filesChecked // "?"' "$GATE_REPORT")"
  G2_FILES="$(jq -r '.gate2.filesChecked // "?"' "$GATE_REPORT")"

  # 2. 與公開 HEAD 比對
  mkdir -p "$TMP/work/head"
  if ! git -C "$PUBLIC_DIR" archive HEAD | tar -x -C "$TMP/work/head"; then
    finish_failure "讀不出公開 repo HEAD 的內容：$PUBLIC_DIR" ""
    echo "=== publish-candidate weekly finished: $(date) ==="; exit 0
  fi
  mv "$STAGE" "$TMP/work/stage"
  ( cd "$TMP/work" && git diff --no-index --name-status --no-renames head stage ) \
    | awk -F'\t' '{ sub(/^(head|stage)\//, "", $2); print $1 "\t" $2 }' > "$TMP/changes.tsv"
  if [ ! -s "$TMP/changes.tsv" ]; then
    f="$TMP/report.out"
    {
      echo "# 公開 harness 每週發布候選 — $DATE"
      echo ""
      if [ -s "$PRIOR" ]; then
        echo "⚠️ 本次重跑匯出結果與公開 repo HEAD（${PUB_SHA}）沒有差異，但同日稍早產生過候選，保留如下；請先確認它是否已發布或處理，再決定。"
        echo ""
        cat "$PRIOR"
      else
        echo "${NO_CANDIDATE}：匯出結果與公開 repo HEAD（${PUB_SHA}）沒有差異；第 1、2 關機械檢查通過。"
      fi
    } > "$f"
    write_report "$f"
    echo "no diff vs $PUB_SHA → report written"
    echo "=== publish-candidate weekly finished: $(date) ==="; exit 0
  fi
  ( cd "$TMP/work" && git diff --no-index --no-color head stage ) > "$TMP/full.diff"
  SHORTSTAT="$( cd "$TMP/work" && git diff --no-index --shortstat head stage )"
  DIFF_TRUNCATED=0
  [ "$(wc -c < "$TMP/full.diff" | tr -d ' ')" -gt "$MAX_DIFF_BYTES" ] && DIFF_TRUNCATED=1
  N_ADD="$(awk -F'\t' '$1 == "A"' "$TMP/changes.tsv" | wc -l | tr -d ' ')"
  N_MOD="$(awk -F'\t' '$1 == "M"' "$TMP/changes.tsv" | wc -l | tr -d ' ')"
  N_DEL="$(awk -F'\t' '$1 == "D"' "$TMP/changes.tsv" | wc -l | tr -d ' ')"

  # 3. 升級全審的條件（前兩項機械判定，第三項等第 3 關結果）
  : > "$TMP/reasons"
  changed_config_files > "$TMP/config-changed" 2> /dev/null
  CONFIG_RC=$?
  if [ "$CONFIG_RC" -eq 2 ]; then
    echo "無法判斷白名單、替換規則、接線點清單自上次發布後有沒有變動（設定檔不在 git 追蹤內）" >> "$TMP/reasons"
  elif [ -s "$TMP/config-changed" ]; then
    echo "白名單、替換規則或接線點清單自上次發布（公開 HEAD ${PUB_SHA}，${PUB_TS}）後有變動：$(paste -sd, "$TMP/config-changed")" >> "$TMP/reasons"
  fi
  awk -F'\t' '$1 == "A" && index($2, "/") { split($2, p, "/"); print p[1] }' "$TMP/changes.tsv" | sort -u | while IFS= read -r seg; do
    [ -e "$TMP/work/head/$seg" ] || echo "新增一整類元件（公開版原本沒有的頂層目錄）：$seg/"
  done >> "$TMP/reasons"

  # 4. 新增項目與第 3 關（模型審查）
  list_new_items > "$TMP/new-items" 2>/dev/null
  NEW_TOTAL="$(wc -l < "$TMP/new-items" | tr -d ' ')"
  NEW_CAPPED=0
  if [ "$NEW_TOTAL" -gt "$MAX_NEW_ITEMS" ]; then
    head -n "$MAX_NEW_ITEMS" "$TMP/new-items" > "$TMP/new-items.cap"; mv "$TMP/new-items.cap" "$TMP/new-items"; NEW_CAPPED=1
  fi
  build_prompt "$TMP/new-items" > "$TMP/prompt.txt"

  G3_STATE=incomplete   # clean | findings | low-confidence | incomplete
  G3_NOTE=""
  run_review < "$TMP/prompt.txt" > "$TMP/review.out" 2> "$TMP/review.err"
  REVIEW_RC=$?
  if [ "$REVIEW_RC" -eq 142 ]; then
    G3_NOTE="逾時（超過 ${REVIEW_TIMEOUT} 秒）"
  elif [ "$REVIEW_RC" -ne 0 ]; then
    G3_NOTE="模型呼叫失敗（結束碼 ${REVIEW_RC}：$(head -n 1 "$TMP/review.err" | one_line | cut -c1-120)）"
  else
    sed 's/^[ *-]*//' "$TMP/review.out" | tr -d '\r' > "$TMP/review.lines"
    RESULTS="$(grep -c '^GATE3_RESULT:' "$TMP/review.lines")"
    VALUE="$(grep -m1 '^GATE3_RESULT:' "$TMP/review.lines" | sed 's/^GATE3_RESULT:[[:space:]]*//; s/[[:space:]]*$//')"
    case "$RESULTS:$VALUE" in
      1:clean|1:findings|1:low-confidence) G3_STATE="$VALUE" ;;
      *) G3_NOTE="輸出格式不符（沒有恰好一行有效的 GATE3_RESULT）" ;;
    esac
  fi

  # 發現與新增檔判定（只在第 3 關有結果時解析）
  : > "$TMP/findings"; : > "$TMP/accepted"; : > "$TMP/include"; : > "$TMP/unclassified"
  if [ "$G3_STATE" != incomplete ]; then
    grep '^FINDING:' "$TMP/review.lines" | sed 's/^FINDING:[[:space:]]*//' | while IFS= read -r line; do
      case "$line" in
        *"$ACCEPTED_PATH"*) printf '%s\n' "$line" >> "$TMP/accepted" ;;
        *) printf '%s\n' "$line" >> "$TMP/findings" ;;
      esac
    done
    if [ "$G3_STATE" = findings ] && [ ! -s "$TMP/findings" ] && [ ! -s "$TMP/accepted" ]; then
      G3_STATE=incomplete; G3_NOTE="回報有發現卻沒有列出任何 FINDING 行"
    elif [ "$G3_STATE" = clean ] && [ -s "$TMP/findings" ]; then
      G3_STATE=findings   # 自相矛盾時以發現為準，不信「clean」
    elif [ "$G3_STATE" = findings ] && [ ! -s "$TMP/findings" ]; then
      G3_STATE=clean      # 只剩使用者已知情接受的項目
    fi
  fi
  if [ "$G3_STATE" != incomplete ]; then
    grep '^NEW_FILE:' "$TMP/review.lines" | sed 's/^NEW_FILE:[[:space:]]*//' > "$TMP/verdicts"
    while IFS= read -r abs; do
      shown="$(display_path "$abs")"
      verdict_line="$(awk -F'|' -v p="$shown" '{ k=$1; gsub(/^[ \t]+|[ \t]+$/, "", k); sub(/\/$/, "", k); if (k == p) { print; exit } }' "$TMP/verdicts")"
      if [ -z "$verdict_line" ]; then echo "$shown" >> "$TMP/unclassified"; continue; fi
      verdict="$(printf '%s' "$verdict_line" | awk -F'|' '{ v=$2; gsub(/^[ \t]+|[ \t]+$/, "", v); print tolower(v) }')"
      reason="$(printf '%s' "$verdict_line" | cut -d'|' -f3- | one_line)"
      case "$verdict" in
        include|進) printf '%s\t%s\n' "$shown" "${reason:-（模型沒給理由）}" >> "$TMP/include" ;;
        exclude|不進) : ;;  # 判「不進」直接生效，不進報告
        *) echo "$shown" >> "$TMP/unclassified" ;;
      esac
    done < "$TMP/new-items"
  fi

  case "$G3_STATE" in
    low-confidence) echo "第 3 關審查回報沒有把握（low-confidence）" >> "$TMP/reasons" ;;
    incomplete) echo "第 3 關未完成（${G3_NOTE}），沒有人審過這份差異中字串比對抓不到的內容" >> "$TMP/reasons" ;;
  esac
  [ "$DIFF_TRUNCATED" -eq 1 ] && echo "差異超過 $MAX_DIFF_BYTES bytes，第 3 關只審了前段" >> "$TMP/reasons"

  # 5. 組報告（候選段永遠最後，同日重跑才抽得回來）
  f="$TMP/report.out"
  {
    echo "# 公開 harness 每週發布候選 — $DATE"
    echo ""
    if [ -s "$PRIOR" ]; then
      echo "⚠️ 本次重新產生了候選，取代同日稍早的版本。"
      echo ""
    fi
    echo "$H_CAND"
    echo ""
    echo "產生時間：$(date '+%Y-%m-%d %H:%M')。是否發布、是否升級全審，都由你決定；agent 不得代做，也不得自行 push 或開始全審。"
    echo "對照基準：公開 repo HEAD ${PUB_SHA}（${PUB_SUBJECT}）。"
    echo ""
    echo "### 差異摘要"
    echo ""
    echo "新增 ${N_ADD}、修改 ${N_MOD}、刪除 $N_DEL 個檔案。${SHORTSTAT:+$(printf '%s' "$SHORTSTAT" | one_line)}"
    for kind in A M D; do
      case "$kind" in A) label="新增" ;; M) label="修改" ;; D) label="刪除" ;; esac
      awk -F'\t' -v k="$kind" '$1 == k { print $2 }' "$TMP/changes.tsv" | head -n 25 | sed "s|^|- ${label}：|"
      total="$(awk -F'\t' -v k="$kind" '$1 == k' "$TMP/changes.tsv" | wc -l | tr -d ' ')"
      [ "$total" -gt 25 ] && echo "- ${label}：…另有 $((total - 25)) 個"
    done
    echo ""
    echo "### 兩關機械檢查"
    echo ""
    echo "- 第 1 關（敏感字掃描）：通過（檢查 $G1_FILES 個檔）"
    echo "- 第 2 關（接線檢查）：通過（檢查 $G2_FILES 個檔）"
    echo ""
    echo "### 第 3 關 agent 差異審查"
    echo ""
    case "$G3_STATE" in
      clean) echo "- 結論：沒有發現。" ;;
      findings) echo "- 結論：有發現，逐項如下。" ;;
      low-confidence) echo "- 結論：審查回報沒有把握，不能當成通過。" ;;
      incomplete) echo "⚠️ 第 3 關未完成：${G3_NOTE}。這不是通過；字串比對抓不到的內容沒有被審過。" ;;
    esac
    if [ -s "$TMP/findings" ]; then sed 's/^/- 發現：/' "$TMP/findings"; fi
    echo ""
    echo "### 新增檔歸類建議（只列判「進」的）"
    echo ""
    if [ "$G3_STATE" = incomplete ] && [ "$NEW_TOTAL" -gt 0 ]; then
      echo "⚠️ 第 3 關未完成，$NEW_TOTAL 個新增項目尚未歸類："
      while IFS= read -r abs; do echo "- $(display_path "$abs")"; done < "$TMP/new-items"
    elif [ -s "$TMP/include" ]; then
      awk -F'\t' '{ print "- " $1 " — " $2 }' "$TMP/include"
    else
      echo "無"
    fi
    if [ -s "$TMP/unclassified" ] && [ "$G3_STATE" != incomplete ]; then
      echo ""
      echo "⚠️ 以下新增項目審查沒有給出判定，尚未歸類："
      sed 's/^/- /' "$TMP/unclassified"
    fi
    [ "$NEW_CAPPED" -eq 1 ] && echo "" && echo "⚠️ 新增項目共 $NEW_TOTAL 個，只送審前 $MAX_NEW_ITEMS 個。"
    echo ""
    echo "### 是否建議升級全審"
    echo ""
    if [ -s "$TMP/reasons" ]; then
      echo "建議升級全審（要不要審由你決定，agent 不會自動開始）："
      sed 's/^/- /' "$TMP/reasons"
    else
      echo "目前沒有觸發條件（白名單、替換規則、接線點清單沒變，沒有新增整類元件，第 3 關有把握），不建議升級全審；仍由你決定。"
    fi
    echo ""
    echo "### 使用者已知情接受"
    echo ""
    if awk -F'\t' -v a="$ACCEPTED_PATH" 'index($2, a)' "$TMP/changes.tsv" | grep -q . || [ -s "$TMP/accepted" ]; then
      echo "- $ACCEPTED_PATH 的上游翻譯／照搬段落（snava10/alist、hughescr/claude-code-config、otomatty/zedi，含 BSL 1.1 內容）：使用者已知情接受（ADR 0004），不列為新發現。"
    else
      echo "無"
    fi
  } > "$f"
  write_report "$f"
  echo "candidate: add=$N_ADD mod=$N_MOD del=$N_DEL gate3=$G3_STATE → report written"

  echo "=== publish-candidate weekly finished: $(date) ==="
  echo "Output: $OUT ($(wc -c < "$OUT") bytes)"
} >> "$LOG" 2>&1
exit 0
