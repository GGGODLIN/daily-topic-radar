#!/usr/bin/env bash
# publish-candidate-weekly.sh 的行為測試：公開 repo、私人來源、報告目錄全導到臨時目錄，不碰真實 ~/.claude 與 reports/。
# 匯出腳本與模型都是系統邊界，以替身取代（匯出替身用 bash 執行、模型替身是假的 claude）；
# 另有一件對真實 export.mjs 的整合檢查，確認 CLI 參數與輸出格式沒有跟替身脫節（找不到腳本就略過並印出）。
# 第 3 關審查「判斷品質」不在這裡測，這裡只驗報告怎麼處理它的各種結果。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WRAPPER="$SCRIPT_DIR/publish-candidate-weekly.sh"
RUNNER="$SCRIPT_DIR/run-shell-channel.sh"
HOOK="${PUBLISH_CANDIDATE_TRIGGER_HOOK:-$HOME/.claude/hooks/daily-local-analysis-trigger.sh}"
REAL_EXPORT="${PUBLISH_CANDIDATE_REAL_EXPORT:-$HOME/.claude/scripts/harness-export/export.mjs}"
TMP="$(mktemp -d)"
PASS=0
FAIL=0
DATE="2026-10-06"
PUB_DATE="2026-10-01T00:00:00+00:00"

cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$1"; }
assert_contains() {
  if printf '%s' "$3" | grep -qF -- "$2"; then pass "$1"; else fail "$1 — missing: $2"; fi
}
assert_not_contains() {
  if printf '%s' "$3" | grep -qF -- "$2"; then fail "$1 — unexpected: $2"; else pass "$1"; fi
}
assert_has_line() {
  if printf '%s\n' "$3" | grep -qxF -- "$2"; then pass "$1"; else fail "$1 — no line: $2"; fi
}

# 跨檔字串只從被測腳本取一份，再分別和報告、hook 比對。
H_CAND="$(sed -n "s/^H_CAND='\(.*\)'\$/\1/p" "$WRAPPER")"
H_FAIL="$(sed -n "s/^H_FAIL='\(.*\)'\$/\1/p" "$WRAPPER")"
NO_CAND="$(sed -n "s/^NO_CANDIDATE='\(.*\)'\$/\1/p" "$WRAPPER")"
LABEL="需要你決策，agent 不得代做"

W="$TMP/w"
git_commit() { # git_commit <repo> <iso date> <message>
  GIT_AUTHOR_DATE="$2" GIT_COMMITTER_DATE="$2" git -C "$1" -c user.name=fixture -c user.email=fixture@example.invalid commit -q -m "$3"
}

# 每個情境重建一份世界：公開 repo（HEAD 在 PUB_DATE）、私人來源 repo（含白名單設定）、替身。
setup_world() {
  rm -rf "$W"
  mkdir -p "$W/pub/hooks" "$W/pub/skills/mutation-testing" "$W/repo" "$W/bin"
  printf 'echo a\n' > "$W/pub/hooks/a.sh"
  printf '# claude\n' > "$W/pub/CLAUDE.md"
  printf 'skill v1\n' > "$W/pub/skills/mutation-testing/SKILL.md"
  git -C "$W/pub" init -q
  git -C "$W/pub" add -A
  git_commit "$W/pub" "$PUB_DATE" "publish: first"

  # stage-src = 公開 HEAD 加上本週改動
  mkdir -p "$W/stage-src"
  git -C "$W/pub" archive HEAD | tar -x -C "$W/stage-src"
  printf 'echo a2\n' > "$W/stage-src/hooks/a.sh"
  printf 'echo extra\n' > "$W/stage-src/hooks/extra.sh"

  local src="$W/home/.claude"
  mkdir -p "$src/hooks" "$src/skills/mutation-testing" "$src/scripts/harness-export/private" "$src/memory"
  printf 'echo a2\n' > "$src/hooks/a.sh"
  printf '# claude\n' > "$src/CLAUDE.md"
  printf 'skill v1\n' > "$src/skills/mutation-testing/SKILL.md"
  printf 'echo old\n' > "$src/hooks/old.sh"
  cat > "$src/scripts/harness-export/private/allowlist.json" <<'JSON'
{"entries":[
  {"source":"~/.claude/hooks/a.sh","path":"hooks/a.sh","category":"mechanism"},
  {"source":"~/.claude/CLAUDE.md","path":"CLAUDE.md","category":"doc"},
  {"source":"~/.claude/skills/mutation-testing","path":"skills/mutation-testing","category":"skill"}
]}
JSON
  printf '{"homePaths":[],"rules":[]}\n' > "$src/scripts/harness-export/private/replace-rules.json"
  printf '{"points":[]}\n' > "$src/scripts/harness-export/private/wiring.json"
  git -C "$src" init -q
  git -C "$src" add -A
  git_commit "$src" "2026-09-01T00:00:00+00:00" "private: base"
  # 上次發布之後才新增（已提交）與還沒提交的項目；memory 目錄是頂層雜訊，不該被當成新元件。
  printf 'echo newer\n' > "$src/hooks/newer.sh"
  git -C "$src" add hooks/newer.sh
  git_commit "$src" "2026-10-03T00:00:00+00:00" "private: add newer"
  printf 'echo new gate\n' > "$src/hooks/new-gate.sh"
  mkdir -p "$src/skills/newskill"
  printf '# newskill\n' > "$src/skills/newskill/SKILL.md"
  printf 'note\n' > "$src/memory/x.md"

  # 匯出替身：以 bash 執行，檢查「只產生不同步」的參數，再依 STUB_EXPORT 造情境。
  cat > "$W/bin/export-stub.sh" <<'STUB'
#!/bin/bash
echo "$*" >> "$STUB_ARGS_LOG"
case " $* " in *" --out "*) echo "stub: --out must never be passed" >&2; exit 2 ;; esac
case " $* " in *" --dry-run "*) ;; *) echo "stub: --dry-run missing" >&2; exit 2 ;; esac
stage=""; report=""
while [ $# -gt 0 ]; do
  case "$1" in --stage-dir) stage="$2"; shift 2 ;; --report) report="$2"; shift 2 ;; *) shift ;; esac
done
good_report() { printf '{"gate1":{"status":"passed","findings":0,"filesChecked":12,"items":[]},"gate2":{"status":"passed","findings":0,"filesChecked":9,"items":[]}}\n' > "$report"; }
case "$STUB_EXPORT" in
  diff) mkdir -p "$stage"; cp -R "$STUB_STAGE_SRC/." "$stage/"; good_report; echo '{"mode":"dry-run"}' ;;
  nodiff) mkdir -p "$stage"; git -C "$STUB_PUB" archive HEAD | tar -x -C "$stage"; good_report; echo '{"mode":"dry-run"}' ;;
  gatefail)
    mkdir -p "$stage"
    printf '{"gate1":{"status":"failed","findings":1,"filesChecked":12,"items":[]},"gate2":{"status":"passed","findings":0,"filesChecked":9,"items":[]}}\n' > "$report"
    printf 'harness-export: mechanical gates failed; nothing was published and the public copy was not touched\ngate 1 (sensitive scan) failed: 1 finding\n  hooks/a.sh:1 needle Acme\nharness-export-gates: {"gate1":{"status":"failed","findings":1},"gate2":{"status":"passed","findings":0}}\n' >&2
    exit 1 ;;
  exportfail) echo "harness-export: allowlist entries with missing source: hooks/missing.sh (source: ~/.claude/hooks/missing.sh)" >&2; exit 1 ;;
  badreport) mkdir -p "$stage"; cp -R "$STUB_STAGE_SRC/." "$stage/"; echo '{"mode":"dry-run"}' ;;
esac
STUB

  # 模型替身：記下 prompt 與路由環境，再依 STUB_REVIEW 回應。
  cat > "$W/bin/claude" <<'STUB'
#!/bin/bash
model=""
while [ $# -gt 0 ]; do case "$1" in --model) model="$2"; shift 2 ;; *) shift ;; esac; done
cat > "$STUB_PROMPT_CAPTURE"
{
  printf 'model=%s\n' "$model"
  printf 'cc_vendor=%s\n' "${CC_VENDOR-}"
  printf 'base_url=%s\n' "${ANTHROPIC_BASE_URL-}"
  printf 'anthropic_model=%s\n' "${ANTHROPIC_MODEL-}"
} > "$STUB_ROUTE_CAPTURE"
case "$STUB_REVIEW" in
  ok)
    echo "GATE3_RESULT: clean"
    echo "NEW_FILE: ~/.claude/hooks/new-gate.sh | include | 通用的 PreToolUse gate，沒有私人內容"
    echo "NEW_FILE: ~/.claude/hooks/newer.sh | exclude | 只服務私人流程"
    echo "NEW_FILE: ~/.claude/skills/newskill/ | include | 通用 skill，沒有身分資訊" ;;
  nonew) echo "GATE3_RESULT: clean" ;;
  findings)
    echo "GATE3_RESULT: findings"
    echo "FINDING: hooks/extra.sh:1 | 內含看起來像內部專案代號的字樣" ;;
  mutation)
    echo "GATE3_RESULT: findings"
    echo "FINDING: skills/mutation-testing/SKILL.md:12 | 段落與上游 zedi 的排錯表相同"
    echo "FINDING: hooks/extra.sh:1 | 內含看起來像內部專案代號的字樣" ;;
  mutation-only)
    echo "GATE3_RESULT: findings"
    echo "FINDING: skills/mutation-testing/SKILL.md:12 | 段落與上游 zedi 的排錯表相同" ;;
  lowconf) echo "GATE3_RESULT: low-confidence" ;;
  garbage) echo "我覺得這份差異看起來沒問題。" ;;
  fail) echo "relay unavailable" >&2; exit 3 ;;
  timeout) exec sleep 30 ;;
esac
STUB
  chmod +x "$W/bin/claude"
}

# run_channel <STUB_EXPORT> <STUB_REVIEW> [extra env ...] — 以乾淨環境跑 wrapper，結束碼放 RC。
run_channel() {
  local export_mode="$1" review_mode="$2"
  shift 2
  rm -f "$W/prompt.capture" "$W/route.capture"
  env -u CC_VENDOR -u ANTHROPIC_BASE_URL \
    STUB_EXPORT="$export_mode" STUB_REVIEW="$review_mode" \
    STUB_STAGE_SRC="$W/stage-src" STUB_PUB="$W/pub" STUB_ARGS_LOG="$W/export.args" \
    STUB_PROMPT_CAPTURE="$W/prompt.capture" STUB_ROUTE_CAPTURE="$W/route.capture" \
    LOCAL_ANALYSIS_DATE="${CHANNEL_DATE:-$DATE}" \
    PUBLISH_CANDIDATE_HOME="$W/home" \
    PUBLISH_CANDIDATE_REPO_DIR="$W/repo" \
    PUBLISH_CANDIDATE_PUBLIC_DIR="$W/pub" \
    PUBLISH_CANDIDATE_NODE=/bin/bash \
    PUBLISH_CANDIDATE_EXPORT_SCRIPT="$W/bin/export-stub.sh" \
    PUBLISH_CANDIDATE_CLAUDE_BIN="$W/bin/claude" \
    PUBLISH_CANDIDATE_REVIEW_TIMEOUT=2 \
    "$@" \
    bash "$WRAPPER" >/dev/null 2>&1
  RC=$?
}
report_file() { printf '%s' "$W/repo/reports/local-analysis/${CHANNEL_DATE:-$DATE}-publish-candidate.md"; }
report() { cat "$(report_file)" 2>/dev/null; }

# ---------------------------------------------------------------- 1. 有差異
setup_world
run_channel diff ok; R="$(report)"
[ "$RC" -eq 0 ] && pass "diff week exits 0" || fail "diff week exits 0 — rc=$RC"
assert_has_line "candidate heading is on its own line" "$H_CAND" "$R"
assert_contains "candidate carries the discuss-only label" "$LABEL" "$R"
assert_contains "candidate names the public HEAD" "公開 repo HEAD $(git -C "$W/pub" log -1 --format=%h)" "$R"
assert_contains "diff summary counts added and modified" "新增 1、修改 1、刪除 0" "$R"
assert_contains "diff summary lists the changed file" "- 修改：hooks/a.sh" "$R"
assert_contains "gate 1 result shown" "第 1 關（敏感字掃描）：通過" "$R"
assert_contains "gate 2 result shown" "第 2 關（接線檢查）：通過" "$R"
assert_contains "gate 3 result shown" "結論：沒有發現" "$R"
assert_not_contains "diff week never writes the no-candidate sentence" "$NO_CAND" "$R"
assert_not_contains "diff week is not a failure" "$H_FAIL" "$R"
assert_contains "dry-run flag was passed to export" "--dry-run" "$(cat "$W/export.args")"
assert_not_contains "export is never asked to sync (--out)" "--out" "$(cat "$W/export.args")"
[ "$(grep -c "$H_CAND" "$(report_file)")" -eq 1 ] && pass "candidate heading appears once" || fail "candidate heading appears once"
# 候選段必須是報告最後一段（同日重跑靠它整段取回）。
[ "$(grep -n '^## ' "$(report_file)" | tail -1 | cut -d: -f2-)" = "## 📦 公開 harness 發布候選（需要你決策，agent 不得代做）" ] && pass "candidate is the last top-level section" || fail "candidate is the last top-level section"

# ---------------------------------------------------------------- 2. 新增檔只列「進」
assert_contains "include item shows path and one-line reason" "- ~/.claude/hooks/new-gate.sh — 通用的 PreToolUse gate，沒有私人內容" "$R"
assert_contains "include item for a new directory (trailing slash tolerated)" "- ~/.claude/skills/newskill — 通用 skill，沒有身分資訊" "$R"
assert_not_contains "exclude verdict takes effect silently (path)" "hooks/newer.sh" "$R"
assert_not_contains "exclude verdict takes effect silently (reason)" "只服務私人流程" "$R"
PROMPT="$(cat "$W/prompt.capture" 2>/dev/null)"
assert_contains "prompt gives the model the new file" "~/.claude/hooks/new-gate.sh" "$PROMPT"
assert_not_contains "files that predate the last publish are not new" "hooks/old.sh" "$PROMPT"
assert_not_contains "top-level noise directories are not new components" "memory" "$PROMPT"
assert_not_contains "allowlisted files are not new" "~/.claude/hooks/a.sh" "$(printf '%s' "$PROMPT" | sed -n '/===== 新增項目 =====/,$p')"
assert_contains "prompt carries the diff" "+echo a2" "$PROMPT"
assert_contains "prompt tells the model about the accepted risk" "ADR 0004" "$PROMPT"
setup_world
rm -rf "$W/home/.claude/hooks/new-gate.sh" "$W/home/.claude/skills/newskill"
rm -f "$W/home/.claude/hooks/newer.sh"
run_channel diff nonew; R="$(report)"
SECTION="$(printf '%s\n' "$R" | sed -n '/^### 新增檔歸類建議/,/^### 是否建議升級全審/p')"
assert_contains "no new files → the section says 無" "無" "$(printf '%s\n' "$SECTION" | sed -n '3p')"

# ---------------------------------------------------------------- 3. 沒有差異
setup_world
run_channel nodiff ok; R="$(report)"
[ "$RC" -eq 0 ] && pass "no-diff week exits 0" || fail "no-diff week exits 0 — rc=$RC"
assert_contains "no-diff report says there is no candidate" "$NO_CAND" "$R"
assert_not_contains "no-diff adds nothing to the top-priority section (candidate heading)" "$H_CAND" "$R"
assert_not_contains "no-diff adds nothing to the top-priority section (failure heading)" "$H_FAIL" "$R"
assert_not_contains "no-diff report does not carry the discuss-only label" "$LABEL" "$R"
[ ! -e "$W/prompt.capture" ] && pass "no-diff week does not call the model" || fail "no-diff week does not call the model"

# ---------------------------------------------------------------- 4. 上週候選未處理 → 本週再次列出
setup_world
CHANNEL_DATE="2026-09-29" run_channel diff ok
CHANNEL_DATE="2026-10-06" run_channel diff ok; R="$(report)"
assert_has_line "unhandled candidate is listed again the next week" "$H_CAND" "$R"
assert_contains "relisted candidate carries the label" "$LABEL" "$R"
[ -s "$W/repo/reports/local-analysis/2026-09-29-publish-candidate.md" ] && pass "last week's report is left alone" || fail "last week's report is left alone"

# ---------------------------------------------------------------- 5. 升級全審
setup_world
run_channel diff ok; R="$(report)"
assert_contains "no trigger → not recommended" "不建議升級全審" "$R"
setup_world
printf '{"points":[{"name":"x"}]}\n' > "$W/home/.claude/scripts/harness-export/private/wiring.json"
git -C "$W/home/.claude" add -A scripts && git_commit "$W/home/.claude" "2026-10-05T00:00:00+00:00" "private: wiring"
run_channel diff ok; R="$(report)"
assert_contains "changed wiring list → recommends full review" "建議升級全審" "$R"
assert_contains "the reason names the changed file" "wiring.json" "$R"
assert_contains "escalation stays the user's choice" "要不要審由你決定，agent 不會自動開始" "$R"
assert_not_contains "recommendation does not claim a review started" "已開始全審" "$R"
setup_world
printf '# edited\n' >> "$W/home/.claude/scripts/harness-export/private/replace-rules.json"
run_channel diff ok; R="$(report)"
assert_contains "uncommitted replace-rules edit → recommends full review" "replace-rules.json" "$R"
setup_world
mkdir -p "$W/stage-src/newcat" && printf 'x\n' > "$W/stage-src/newcat/file.md"
run_channel diff ok; R="$(report)"
assert_contains "new top-level directory → new component category" "新增一整類元件（公開版原本沒有的頂層目錄）：newcat/" "$R"
setup_world
run_channel diff lowconf; R="$(report)"
assert_contains "low confidence → recommends full review" "第 3 關審查回報沒有把握" "$R"
assert_not_contains "low confidence is never shown as passed" "結論：沒有發現" "$R"
setup_world
cp -R "$W/home/.claude/scripts/harness-export/private" "$W/cfg-nogit"
run_channel diff ok PUBLISH_CANDIDATE_CONFIG_DIR="$W/cfg-nogit"; R="$(report)"
assert_contains "config outside git → cannot tell, recommends full review" "無法判斷白名單、替換規則、接線點清單" "$R"

# ---------------------------------------------------------------- 6. 匯出或機械關卡失敗
for mode in gatefail exportfail badreport; do
  setup_world
  run_channel "$mode" ok; R="$(report)"
  [ "$RC" -eq 0 ] && pass "$mode: wrapper exits 0 so the report survives the runner" || fail "$mode: wrapper exits 0 — rc=$RC"
  assert_has_line "$mode: failure heading shown" "$H_FAIL" "$R"
  assert_not_contains "$mode: no candidate heading" "$H_CAND" "$R"
  assert_not_contains "$mode: no discuss-only candidate label" "$LABEL" "$R"
  assert_not_contains "$mode: not reported as no-candidate week" "$NO_CAND" "$R"
  [ ! -e "$W/prompt.capture" ] && pass "$mode: model is not called" || fail "$mode: model is not called"
done
setup_world
run_channel gatefail ok; R="$(report)"
assert_contains "gate failure detail names the finding" "hooks/a.sh:1" "$R"
setup_world
run_channel exportfail ok; R="$(report)"
assert_contains "export failure detail is kept" "allowlist entries with missing source" "$R"
setup_world
run_channel badreport ok; R="$(report)"
assert_contains "missing gate report is treated as not passed" "兩關機械檢查結果讀不到或未通過" "$R"
setup_world
run_channel diff ok PUBLISH_CANDIDATE_PUBLIC_DIR="$W/not-a-repo"; R="$(report)"
assert_has_line "unreadable public HEAD is a failure" "$H_FAIL" "$R"

# ---------------------------------------------------------------- 7. 第 3 關失敗、逾時、格式不符
for mode in fail timeout garbage; do
  setup_world
  run_channel diff "$mode"; R="$(report)"
  assert_contains "$mode: report says gate 3 not completed" "第 3 關未完成" "$R"
  assert_not_contains "$mode: gate 3 is not shown as passed" "結論：沒有發現" "$R"
  assert_has_line "$mode: candidate is still surfaced (gates 1-2 passed)" "$H_CAND" "$R"
  assert_contains "$mode: escalation recommended" "建議升級全審" "$R"
  assert_contains "$mode: new files are not silently dropped" "尚未歸類" "$R"
  assert_not_contains "$mode: no include verdict is invented" "— 通用" "$R"
done
setup_world
run_channel diff timeout; R="$(report)"
assert_contains "timeout is named as such" "逾時" "$R"
setup_world
run_channel diff fail; R="$(report)"
assert_contains "failure carries the model error" "relay unavailable" "$R"

# ---------------------------------------------------------------- 8. 使用者已知情接受（mutation-testing）
setup_world
printf 'skill v2\n' > "$W/stage-src/skills/mutation-testing/SKILL.md"
run_channel diff mutation; R="$(report)"
assert_contains "accepted-risk section names ADR 0004" "使用者已知情接受（ADR 0004），不列為新發現" "$R"
assert_not_contains "mutation-testing upstream passage is not listed as a finding" "發現：skills/mutation-testing" "$R"
assert_not_contains "mutation-testing upstream passage is not listed as a finding (text)" "排錯表" "$R"
assert_contains "other findings are still listed" "發現：hooks/extra.sh:1" "$R"
assert_contains "gate 3 says there are findings" "結論：有發現" "$R"
setup_world
run_channel diff mutation-only; R="$(report)"
assert_contains "only accepted-risk findings → no new finding" "結論：沒有發現" "$R"
assert_contains "only accepted-risk findings → still annotated" "使用者已知情接受（ADR 0004）" "$R"
setup_world
run_channel diff ok; R="$(report)"
assert_contains "untouched mutation-testing → accepted section says 無" "無" "$(printf '%s\n' "$R" | sed -n '/^### 使用者已知情接受/,$p' | sed -n '3p')"

# ---------------------------------------------------------------- 9. 報告 heading 被 trigger hook 的排檔條件比中
if [ -f "$HOOK" ]; then
  CTX="$(printf '{"prompt":"每日本機分析"}' | bash "$HOOK" 2>/dev/null | jq -r '.hookSpecificOutput.additionalContext // ""')"
  setup_world
  run_channel diff ok; R="$(report)"
  assert_contains "hook injection contains the candidate heading verbatim" "$H_CAND" "$CTX"
  assert_contains "hook injection contains the failure heading verbatim" "$H_FAIL" "$CTX"
  assert_contains "hook injection names the report file pattern" "-publish-candidate.md" "$CTX"
  assert_has_line "the report's heading is the string the hook matches" "$H_CAND" "$R"
  run_channel gatefail ok; R="$(report)"
  assert_has_line "the failure report's heading is the string the hook matches" "$H_FAIL" "$R"
  assert_contains "hook tells main not to act on the candidate" "$LABEL" "$CTX"
else
  fail "trigger hook not found at $HOOK"
fi

# ---------------------------------------------------------------- 10. 同日手動提早跑 + 排程跑
setup_world
run_channel diff ok
run_channel nodiff ok; R="$(report)"
assert_has_line "same-day no-diff rerun keeps the earlier candidate" "$H_CAND" "$R"
assert_not_contains "same-day no-diff rerun never says there is no candidate" "$NO_CAND" "$R"
assert_contains "same-day rerun explains the earlier candidate is kept" "同日稍早產生過候選" "$R"
run_channel nodiff ok; R="$(report)"
assert_has_line "a third run still keeps it" "$H_CAND" "$R"
[ "$(grep -c "$H_CAND" "$(report_file)")" -eq 1 ] && pass "kept candidate is not duplicated" || fail "kept candidate is not duplicated"

setup_world
run_channel diff ok
run_channel gatefail ok; R="$(report)"
assert_has_line "same-day failed rerun shows the failure" "$H_FAIL" "$R"
assert_has_line "same-day failed rerun keeps the earlier candidate" "$H_CAND" "$R"
assert_contains "same-day failed rerun says the kept candidate was not re-verified" "沒有重新驗證" "$R"

setup_world
run_channel diff ok
printf 'echo changed again\n' > "$W/stage-src/hooks/extra.sh"
run_channel diff ok; R="$(report)"
assert_has_line "a fresh candidate replaces the earlier one" "$H_CAND" "$R"
assert_contains "replacement says so" "取代同日稍早的版本" "$R"
[ "$(grep -c "$H_CAND" "$(report_file)")" -eq 1 ] && pass "replacement keeps a single candidate" || fail "replacement keeps a single candidate"

# 排程經 run-shell-channel.sh：它會先把既有報告搬成 .previous，wrapper 也要從那裡取回。
setup_world
run_channel diff ok   # 手動提早跑（直接呼叫 wrapper，沒有完成標記）
env -u CC_VENDOR -u ANTHROPIC_BASE_URL \
  STUB_EXPORT=nodiff STUB_REVIEW=ok STUB_STAGE_SRC="$W/stage-src" STUB_PUB="$W/pub" STUB_ARGS_LOG="$W/export.args" \
  STUB_PROMPT_CAPTURE="$W/prompt.capture" STUB_ROUTE_CAPTURE="$W/route.capture" \
  PUBLISH_CANDIDATE_HOME="$W/home" PUBLISH_CANDIDATE_REPO_DIR="$W/repo" PUBLISH_CANDIDATE_PUBLIC_DIR="$W/pub" \
  PUBLISH_CANDIDATE_NODE=/bin/bash PUBLISH_CANDIDATE_EXPORT_SCRIPT="$W/bin/export-stub.sh" \
  PUBLISH_CANDIDATE_CLAUDE_BIN="$W/bin/claude" PUBLISH_CANDIDATE_REVIEW_TIMEOUT=2 \
  bash "$RUNNER" "$WRAPPER" "$(report_file)" "$DATE" false >/dev/null 2>&1
RUNNER_RC=$?
R="$(report)"
[ "$RUNNER_RC" -eq 0 ] && pass "scheduled run via the runner exits 0" || fail "scheduled run via the runner exits 0 — rc=$RUNNER_RC"
assert_has_line "scheduled run after a manual run keeps the candidate" "$H_CAND" "$R"
assert_not_contains "scheduled run never overwrites it with no-candidate" "$NO_CAND" "$R"

# ---------------------------------------------------------------- 11. 模型路由
setup_world
run_channel diff ok
ROUTE="$(cat "$W/route.capture" 2>/dev/null)"
assert_contains "native route uses opus" "model=opus" "$ROUTE"
assert_contains "native route runs as a headless channel" "cc_vendor=headless-channel" "$ROUTE"
cat > "$W/keys.env" <<'KEYS'
CLIPROXY_BASE_URL=http://127.0.0.1:8317
CLIPROXY_KEY_CC=fixture-token
KEYS
printf 'GPT_LUNA=fixture-luna\n' > "$W/gpt-models.env"
run_channel diff ok CC_VENDOR=ccp-free PUBLISH_CANDIDATE_KEYS_FILE="$W/keys.env" PUBLISH_CANDIDATE_GPT_MODELS_FILE="$W/gpt-models.env"
ROUTE="$(cat "$W/route.capture" 2>/dev/null)"
assert_contains "relay route uses Luna(max)" "model=fixture-luna(max)" "$ROUTE"
assert_contains "relay route goes to the local relay" "base_url=http://127.0.0.1:8317" "$ROUTE"
printf 'CLIPROXY_BASE_URL=http://example.invalid:8317\nCLIPROXY_KEY_CC=x\n' > "$W/keys.env"
run_channel diff ok CC_VENDOR=ccp-free PUBLISH_CANDIDATE_KEYS_FILE="$W/keys.env" PUBLISH_CANDIDATE_GPT_MODELS_FILE="$W/gpt-models.env"; R="$(report)"
assert_contains "non-local relay URL is refused → gate 3 not completed" "第 3 關未完成" "$R"

# ---------------------------------------------------------------- 12. 真實 export.mjs 整合（CLI 參數與輸出格式沒有跟替身脫節）
REAL_NODE="$(command -v node || echo /opt/homebrew/bin/node)"
if [ -f "$REAL_EXPORT" ] && [ -x "$REAL_NODE" ]; then
  setup_world
  RH="$W/real-home"; mkdir -p "$RH/.claude/hooks" "$RH/cfg"
  printf '#!/bin/bash\necho real\n' > "$RH/.claude/hooks/a.sh"
  printf '{"entries":[{"source":"~/.claude/hooks/a.sh","path":"hooks/a.sh","category":"mechanism"}]}\n' > "$RH/cfg/allowlist.json"
  env -u CC_VENDOR -u ANTHROPIC_BASE_URL \
    STUB_REVIEW=nonew STUB_PROMPT_CAPTURE="$W/prompt.capture" STUB_ROUTE_CAPTURE="$W/route.capture" \
    LOCAL_ANALYSIS_DATE="$DATE" PUBLISH_CANDIDATE_HOME="$RH" PUBLISH_CANDIDATE_REPO_DIR="$W/repo" \
    PUBLISH_CANDIDATE_PUBLIC_DIR="$W/pub" PUBLISH_CANDIDATE_NODE="$REAL_NODE" PUBLISH_CANDIDATE_EXPORT_SCRIPT="$REAL_EXPORT" \
    PUBLISH_CANDIDATE_CONFIG_DIR="$RH/cfg" PUBLISH_CANDIDATE_CLAUDE_BIN="$W/bin/claude" \
    bash "$WRAPPER" >/dev/null 2>&1
  R="$(report)"
  assert_has_line "real export.mjs dry-run → candidate against the public HEAD" "$H_CAND" "$R"
  assert_contains "real export.mjs → gate 1 passed" "第 1 關（敏感字掃描）：通過" "$R"
  assert_contains "real export.mjs → diff vs the public HEAD is counted" "新增 0、修改 1、刪除 2" "$R"
  [ -z "$(git -C "$W/pub" status --porcelain)" ] && pass "real export dry-run leaves the public copy untouched" || fail "real export dry-run leaves the public copy untouched"
else
  printf 'SKIP: real export.mjs integration (not found: %s)\n' "$REAL_EXPORT"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
