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
# 對照基準：優先用公開 repo 本機的 refs/remotes/origin/main，沒有才退回 HEAD。
# 原因：發布流程是「先本機 commit、再過使用者可拒絕的 gate 才 push」，push 也可能被 GitHub push protection 退回；
# 若以本機 HEAD 為基準，尚未推上去的匯出內容會讓下週比對出「沒有差異」而靜默消失。本機另保留一條無關歷史的舊 main 分支，HEAD 被切過去時基準也會變垃圾。
# 本 channel 唯讀、不 fetch、不連網，所以 origin/main 只和最後一次 fetch 一樣新；報告會註明，並在 HEAD 與 origin/main 不同時多標一行 ⚠️。
#
# 第 3 關的提示、模型路由（原生 Claude 用 native Opus、其餘啟動方式走本機 CLIProxyAPI 的 Luna(max)）與判讀都在
# ~/.claude/scripts/harness-export/gate3-review.sh，與 publish.sh 共用；路由仍與 recap-daily.sh 的 run_recap 同一套。
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
# 第 3 關的提示、模型路由與判讀和 publish.sh 共用這一支；用真正的 $HOME 而非 HOME_DIR，測試換假家目錄時仍載入同一份。
GATE3_LIB="${PUBLISH_CANDIDATE_GATE3_LIB:-$HOME/.claude/scripts/harness-export/gate3-review.sh}"
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
  gate3_instructions
  echo ""
  echo "===== 差異（unified diff，head/ = 公開 repo ${BASE_NAME} ${PUB_SHA}，stage/ = 本次匯出）====="
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

# ---- main ------------------------------------------------------------------

{
  echo "=== publish-candidate weekly started: $(date) ==="

  PRIOR="$TMP/prior.md"
  extract_prior_candidate > "$PRIOR"

  # shellcheck source=/dev/null
  if ! source "$GATE3_LIB"; then
    one_line() { tr '\r\n' '  ' | sed 's/  */ /g; s/^ //; s/ $//'; }
    finish_failure "第 3 關共用腳本讀不到：$GATE3_LIB" ""
    echo "=== publish-candidate weekly finished: $(date) ==="; exit 0
  fi

  BASE_REF=HEAD; BASE_NAME=HEAD
  if [ -d "$PUBLIC_DIR" ] && git -C "$PUBLIC_DIR" rev-parse --verify --quiet 'refs/remotes/origin/main^{commit}' >/dev/null 2>&1; then
    BASE_REF=refs/remotes/origin/main; BASE_NAME=origin/main
  fi
  if [ ! -d "$PUBLIC_DIR" ] || ! git -C "$PUBLIC_DIR" rev-parse --verify --quiet "${BASE_REF}^{commit}" >/dev/null 2>&1; then
    finish_failure "公開 repo 本機副本讀不到 ${BASE_NAME}：$PUBLIC_DIR" ""
    echo "=== publish-candidate weekly finished: $(date) ==="; exit 0
  fi
  PUB_SHA="$(git -C "$PUBLIC_DIR" log -1 --format=%h "$BASE_REF")"
  PUB_SUBJECT="$(git -C "$PUBLIC_DIR" log -1 --format=%s "$BASE_REF")"
  PUB_TS="$(git -C "$PUBLIC_DIR" log -1 --format=%cI "$BASE_REF")"
  BASE_NOTES="$TMP/base-notes.txt"; : > "$BASE_NOTES"
  if [ "$BASE_NAME" = origin/main ]; then
    echo "origin/main 只和本機上次 fetch 一樣新（本 channel 刻意不 fetch、不連網）；遠端之後若有新 commit，這裡看不到。" >> "$BASE_NOTES"
    PUB_HEAD_FULL="$(git -C "$PUBLIC_DIR" rev-parse --verify --quiet 'HEAD^{commit}' 2>/dev/null || true)"
    if [ "$PUB_HEAD_FULL" != "$(git -C "$PUBLIC_DIR" rev-parse "$BASE_REF")" ]; then
      PUB_HEAD_SHORT="$(git -C "$PUBLIC_DIR" rev-parse --short HEAD 2>/dev/null || echo 讀不到)"
      echo "⚠️ 公開 repo 本機副本的 HEAD 與 origin/main 不同：HEAD（${PUB_HEAD_SHORT}）、origin/main（${PUB_SHA}）。可能有尚未推上去的本機 commit，或 HEAD 被切到別的分支；本次以 origin/main 為基準，請確認那些本機 commit 是否還要發布。" >> "$BASE_NOTES"
    fi
  fi

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

  # 2. 與公開基準（origin/main，沒有就 HEAD）比對
  mkdir -p "$TMP/work/head"
  if ! git -C "$PUBLIC_DIR" archive "$BASE_REF" | tar -x -C "$TMP/work/head"; then
    finish_failure "讀不出公開 repo ${BASE_NAME} 的內容：$PUBLIC_DIR" ""
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
        echo "⚠️ 本次重跑匯出結果與公開 repo ${BASE_NAME}（${PUB_SHA}）沒有差異，但同日稍早產生過候選，保留如下；請先確認它是否已發布或處理，再決定。"
        [ -s "$BASE_NOTES" ] && { echo ""; cat "$BASE_NOTES"; }
        echo ""
        cat "$PRIOR"
      else
        echo "${NO_CANDIDATE}：匯出結果與公開 repo ${BASE_NAME}（${PUB_SHA}）沒有差異；第 1、2 關機械檢查通過。"
        [ -s "$BASE_NOTES" ] && { echo ""; cat "$BASE_NOTES"; }
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
    echo "白名單、替換規則或接線點清單自上次發布（公開 ${BASE_NAME} ${PUB_SHA}，${PUB_TS}）後有變動：$(paste -sd, "$TMP/config-changed")" >> "$TMP/reasons"
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

  run_review < "$TMP/prompt.txt" > "$TMP/review.out" 2> "$TMP/review.err"
  gate3_parse "$?" "$TMP/review.out" "$TMP/review.err" "$TMP"

  # 新增檔判定（只在第 3 關有結果時解析）
  : > "$TMP/include"; : > "$TMP/unclassified"
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
    echo "對照基準：公開 repo ${BASE_NAME} ${PUB_SHA}（${PUB_SUBJECT}）。"
    [ -s "$BASE_NOTES" ] && cat "$BASE_NOTES"
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
      # ADR 0004 只接受上游授權風險；分類是子字串比對，這個路徑下的隱私類發現也會落到這裡，所以原文逐行印出讓使用者自己判斷。
      if [ -s "$TMP/accepted" ]; then
        echo "- 第 3 關回報中被歸入此類的 FINDING 原文（請確認只有上游授權風險，沒有洩漏類發現）："
        sed 's/^/  - /' "$TMP/accepted"
      fi
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
